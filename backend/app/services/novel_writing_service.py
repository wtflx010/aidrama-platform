"""AI 长篇小说写作服务：大纲规划 + 逐章正文生成（供 Celery write_novel_task 调用）。

流程：
1. generate_outline() → LLM 规划 N 章大纲，写入 novel.outline（JSONB）
2. write_chapter() × N → 逐章生成正文（每章 1500~2500 字，带前情提要保证连贯）
3. append_chapter() → 章节追加进 raw_text（章节标记用【第X章 标题】独占一行，
   兼容 novel_analysis_service.split_chapters，可直接走「分析→改编剧本」下游）
"""
import json
import re

from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.models.novel import Novel
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services import writing_style

# 章节标记行：独占一行，兼容 split_chapters 的【.+?】识别
def chapter_marker(index: int, title: str) -> str:
    return f"【第{index}章 {title}】"


OUTLINE_PROMPT = """你是一名资深长篇小说编辑。根据用户提供的题材设定，规划一部长篇小说的完整分章大纲。

要求输出严格 JSON（不要 markdown 代码块、不要 JSON 之外的任何文字），结构如下：
{{
  "title": "小说标题(≤20字)",
  "genre": "题材类型（如：古风悬疑）",
  "logline": "一句话故事梗概（≤40字）",
  "world": "世界观/核心设定（≤150字，供每章保持一致）",
  "chapters": [
    {{"index": 1, "title": "本章标题(≤10字)", "brief": "本章剧情要点（≤80字，讲清发生什么、关键冲突/悬念）"}}
  ]
}}

要求：
- 共 {chapters} 章，章章连贯，构成完整故事弧线（起因→发展→高潮→反转→结局）
- 每章 brief 具体可执行，让写手能直接按此展开
- 题材类型与用户设定保持一致

{style_block}"""

CHAPTER_PROMPT = """你是一名擅长{genre}的长篇小说作家。请按大纲撰写小说章节正文。

【写作要求】
1. 第一行必须是章节标记行：{marker}
2. 正文 1500~2500 字，简体中文
3. 情节紧凑、画面感强、对白自然，落实本章 brief 中的冲突/悬念
4. 延续前情：角色、伏笔、人物关系必须与前情提要一致，不得跳戏
5. 结尾留下钩子，自然衔接下一章
6. 正文中禁止使用【】括号行（如【背景资料】等），档案摘录请用普通段落书写
7. 只输出章节正文本身，不要任何解释或评论

{style_block}

【小说核心设定】
{world}

【本章大纲】
标题：{title}
剧情要点：{brief}

【前情提要】
{prev_summary}"""


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def plan_outline(
    db: Session, brief: str, genre: str, chapters: int, model_id=None,
    style_mode: str | None = None,
) -> dict:
    """LLM 规划分章大纲（不落库），返回 {title, genre, logline, world, chapters}。"""
    if not brief or not brief.strip():
        raise ValueError("题材设定不能为空")
    if chapters < 1 or chapters > 100:
        raise ValueError("章数需在 1~100 之间")
    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    resp = provider.chat(
        [
            {"role": "system", "content": OUTLINE_PROMPT.format(
                        chapters=chapters,
                        style_block=writing_style.build_inject_block(style_mode, include_anchor=False),
                    )},
            {
                "role": "user",
                "content": f"题材设定：{brief.strip()}\n类型：{genre or '未指定'}",
            },
        ]
    )
    raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse_json(raw)
    chapters_list = data.get("chapters") or []
    if len(chapters_list) < chapters:
        raise ValueError(
            f"大纲章节数不足（收到 {len(chapters_list)} 章，期望 {chapters} 章），请重试"
        )
    chapters_list = chapters_list[:chapters]  # 输出多于设定时截断到设定章数
    return {
        "title": (data.get("title") or "").strip()[:40],
        "genre": (data.get("genre") or genre or "小说").strip()[:30],
        "logline": (data.get("logline") or "").strip()[:80],
        "world": (data.get("world") or "").strip()[:500],
        "chapters": [
            {
                "index": i + 1,
                "title": (c.get("title") or f"第{i + 1}章").strip()[:20],
                "brief": (c.get("brief") or "").strip()[:200],
            }
            for i, c in enumerate(chapters_list)
        ],
    }


def generate_outline(
    db: Session, novel: Novel, brief: str, genre: str, chapters: int,
    model_id=None, style_mode: str | None = None,
) -> dict:
    """LLM 规划分章大纲并写入 novel.outline。返回 outline dict。"""
    out = plan_outline(db, brief, genre, chapters, model_id, style_mode=style_mode)
    if not out.get("title"):
        out["title"] = novel.title
    novel.outline = out
    db.commit()
    return out


def write_chapter(db: Session, novel: Novel, index: int, model_id=None, style_mode: str | None = None) -> str:
    """生成第 index 章正文（index 从 1 开始）。只返回正文文本，不落库（由调用方 append）。"""
    outline = novel.outline or {}
    chapters = outline.get("chapters") or []
    if index < 1 or index > len(chapters):
        raise ValueError(f"章节序号 {index} 超出大纲范围（1~{len(chapters)}）")
    ch = chapters[index - 1]

    # 前情提要：前面章节 title+brief 拼接（最多 10 章，控制 token）
    prev_parts = [
        f"第{c['index']}章《{c['title']}》：{c.get('brief') or ''}"
        for c in chapters[: index - 1]
    ]
    prev_summary = "\n".join(prev_parts[-10:]) or "（本章为开篇章节，无前情）"

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    marker = chapter_marker(index, ch["title"])
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": CHAPTER_PROMPT.format(
                    genre=outline.get("genre") or "小说",
                    marker=marker,
                    style_block=writing_style.build_inject_block(style_mode, include_anchor=True),
                    world=outline.get("world") or "",
                    title=ch["title"],
                    brief=ch["brief"],
                    prev_summary=prev_summary,
                ),
            },
            {"role": "user", "content": f"请撰写第{index}章《{ch['title']}》正文。"},
        ]
    )
    text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = text.strip()
    if not text:
        raise ValueError("章节生成内容为空")
    # 保证章节标记行在开头（模型可能输出围栏或换行）
    if marker not in text.split("\n")[0]:
        text = marker + "\n\n" + text
    return text


def append_chapter(db: Session, novel: Novel, text: str) -> None:
    """章节追加进 raw_text，并用 split_chapters 刷新章节数/字数。"""
    from app.services.novel_analysis_service import split_chapters

    text = text.strip()
    if novel.raw_text.strip():
        novel.raw_text = novel.raw_text.rstrip() + "\n\n" + text
    else:
        novel.raw_text = text
    chapters = split_chapters(novel.raw_text)
    novel.chapters_count = len(chapters)
    novel.word_count = len(novel.raw_text)
    db.commit()
