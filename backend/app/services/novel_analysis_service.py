"""小说内容分析服务：Map-Reduce 分段摘要 + 核心情节提取。

流程：
1. split_chapters() → 按「第X章」分割
2. summarize_chapter() × N → 逐章 LLM 摘要（Map，线程池并行）
3. extract_plot_elements() → 合并摘要 → 全局信息提取（Reduce）
4. 写入 Novel.analysis_result

章节分割支持：第X章/第X回/第X节/Chapter N/【第X章】，无标记按 3000 字均分。
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.models.novel import Novel, NovelAnalysisStatus
from app.providers.errors import map_to_chinese
from app.services.keyframe_service import _resolve_model

# 章节标题正则：匹配「第一章」「第1章」「第十二回」「Chapter 1」「【第一章】」等
CHAPTER_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十百千零〇\d]+[章回节卷]|Chapter\s+\d+|【.+?】)\s*$",
    re.MULTILINE,
)

# 无章节标记时的分段大小
CHUNK_SIZE = 3000

CHAPTER_SUMMARY_PROMPT = """你是一名专业的剧本改编助手。请阅读以下小说章节，提取核心信息。

要求返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构如下：
{{
  "title": "章节标题",
  "summary": "200字以内的章节摘要，包含核心事件和角色行为",
  "characters": ["本章出场的角色名"],
  "scenes": ["本章出现的场景或地点"],
  "emotion": "本章主导情感（平静/紧张/悲伤/温馨/愤怒/欢快/恐惧/史诗）",
  "intensity": 1到10的整数，表示情感强度,
  "key_events": ["关键事件1", "关键事件2"]
}}

章节内容：
{chapter_text}"""

PLOT_EXTRACTION_PROMPT = """你是一名专业的短剧策划。基于以下各章摘要，提取完整的故事结构。

要求返回纯 JSON（不要 markdown 代码块），结构如下：
{{
  "outline": "300到800字的故事大纲，按三幕结构（建置、对抗、结局）组织",
  "characters": [
    {{
      "name": "角色名",
      "role": "角色定位（女主角/男主角/反派/配角）",
      "personality": "性格描述",
      "appearance": "外貌描述（年龄、发型、服饰等）",
      "arc": "角色成长弧（从什么到什么）"
    }}
  ],
  "scenes": [
    {{
      "name": "场景名",
      "description": "场景描述（地点、环境、氛围）",
      "mood": "氛围关键词"
    }}
  ],
  "emotion_curve": [
    {{"chapter": 1, "emotion": "平静", "intensity": 3}}
  ],
  "core_conflict": {{
    "protagonist": "主角",
    "antagonist": "对手（人或抽象力量）",
    "stake": "赌注（如果失败会怎样）"
  }}
}}

各章摘要：
{merged_summaries}"""


def split_chapters(text: str) -> list[dict]:
    """按章节标记分割小说。

    支持格式：第X章/第X回/第X节/Chapter N/【标题】
    无标记时按字数均分（每段约 3000 字）。
    """
    matches = list(CHAPTER_PATTERN.finditer(text))
    if len(matches) < 2:
        # 无章节标记 → 按字数均分
        chunks = [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        return [
            {"index": i + 1, "title": f"段落{i + 1}", "text": c.strip()}
            for i, c in enumerate(chunks)
            if c.strip()
        ]

    chapters = []
    for i, m in enumerate(matches):
        title = m.group(0).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chapters.append({"index": i + 1, "title": title, "text": body})
    return chapters


def summarize_chapter(chapter: dict, provider, max_retries: int = 3) -> dict:
    """Map 阶段：单章 LLM 摘要（含重试）。

    截断到 8000 字防超长（大部分 LLM 上限 32k+，但质量在超长时衰减）。
    LLM 偶发返回格式异常时自动重试（默认 3 次），每次间隔 2 秒。
    """
    from app.services.llm_script_service import _extract_json

    text = chapter["text"][:8000]
    prompt = CHAPTER_SUMMARY_PROMPT.format(chapter_text=text)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise
        try:
            content = resp["choices"][0]["message"]["content"]
            result = _extract_json(content)
            result["index"] = chapter["index"]
            return result
        except Exception as e:
            snippet = ""
            try:
                snippet = (resp["choices"][0]["message"]["content"] or "")[:300]
            except Exception:
                pass
            last_err = ValueError(
                f"章节摘要 JSON 解析失败（第{attempt}/{max_retries}次）：{e}；原始片段：{snippet!r}"
            )
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise last_err
    raise last_err  # type: ignore[misc]


def extract_plot_elements(summaries: list[dict], provider, max_retries: int = 3) -> dict:
    """Reduce 阶段：从合并摘要提取全局信息（大纲/角色/场景/情感曲线/核心冲突）。

    含重试机制：LLM 偶发返回格式异常时自动重试（默认 3 次）。
    """
    from app.services.llm_script_service import _extract_json

    merged = "\n\n".join(
        f"第{s.get('index', i + 1)}章 {s.get('title', '')}：{s.get('summary', '')}"
        for i, s in enumerate(summaries)
    )
    prompt = PLOT_EXTRACTION_PROMPT.format(merged_summaries=merged)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise
        try:
            content = resp["choices"][0]["message"]["content"]
            result = _extract_json(content)
            result["chapters_summary"] = summaries
            return result
        except Exception as e:
            snippet = ""
            try:
                snippet = (resp["choices"][0]["message"]["content"] or "")[:300]
            except Exception:
                pass
            last_err = ValueError(
                f"全局提取 JSON 解析失败（第{attempt}/{max_retries}次）：{e}；原始片段：{snippet!r}"
            )
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise last_err
    raise last_err  # type: ignore[misc]


def analyze_novel(db: Session, novel_id: str, model_id=None) -> Novel:
    """编排完整分析流程（由 Celery 任务调用）。

    1. 读取小说全文
    2. split_chapters → 分章
    3. 并行 summarize_chapter × N（线程池 max_workers=3）
    4. extract_plot_elements → 全局提取
    5. 写入 Novel.analysis_result
    """
    from app.providers.registry import ProviderRegistry

    novel = db.get(Novel, novel_id)
    if not novel:
        raise ValueError("小说不存在")

    novel.analysis_status = NovelAnalysisStatus.analyzing
    db.commit()

    # 1. 分章
    chapters = split_chapters(novel.raw_text)
    novel.chapters_count = len(chapters)
    novel.word_count = len(novel.raw_text)
    db.commit()

    if not chapters:
        raise ValueError("小说内容为空，无法分析")

    # 2. 定位 LLM 模型
    model = _resolve_model(db, model_id, ModelType.text, "script")

    # 3. 并行摘要（Map）—— 主线程预解析 provider（httpx client 线程安全），
    # 避免 worker 线程并发使用同一个 SQLAlchemy Session（Session 非线程安全，
    # for_model_id 内部会 db.get(Model)，此前 3 线程并发共享 db 存在竞态）
    summaries: list[dict | None] = [None] * len(chapters)
    provider = ProviderRegistry.for_model_id(db, model.id)

    def _do_summarize(idx: int, chapter: dict) -> dict:
        return summarize_chapter(chapter, provider)

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_idx = {pool.submit(_do_summarize, i, ch): i for i, ch in enumerate(chapters)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                summaries[idx] = future.result()
            except Exception as e:
                # 单章失败不阻断整体，记录错误摘要
                summaries[idx] = {
                    "index": idx + 1,
                    "title": chapters[idx]["title"],
                    "summary": f"(摘要失败: {map_to_chinese(e)})",
                    "characters": [],
                    "scenes": [],
                    "emotion": "平静",
                    "intensity": 1,
                    "key_events": [],
                }

    # 4. 全局提取（Reduce）
    provider = ProviderRegistry.for_model_id(db, model.id)
    result = extract_plot_elements([s for s in summaries if s], provider)

    # 5. 写入
    novel.analysis_result = result
    novel.analysis_status = NovelAnalysisStatus.done
    novel.error = None
    db.commit()
    return novel
