"""AI 分集剧本写作服务：集纲规划 + 逐集场次级完整剧本 + 逐集追加为项目幕。

流程（供 Celery write_script_task 调用）：
1. plan_episode_outline() → LLM 规划 N 集集纲（每集：标题/要点/冲突/涉及角色场景）
2. write_episode_script() × N → 逐集生成场次级完整剧本（场景/动作/对白/旁白，1000~1800 字/集，带前情提要）
3. structure_episode() → 每集剧本转结构化幕（materialize_draft 格式：segments + assets）
4. apply_episode() → materialize_draft 追加该集为新幕（第 1 集时新建项目），并把完整剧本逐集追加进 project.script

与 create_project（基于已有剧本一次性生成 1~N 幕）的区别：
- write_script 支持大规模逐集产出：每集完整剧本落为一幕，项目幕数 = 集数，可写 1~100 集
- 产出主体是「编剧」专业角色（独立 persona），保证剧本文本质量

集纲持久化：写入 Task.provider_task_id（JSON），任务失败重跑时读取复用，保证续写前情一致。
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.models.project import Project
from app.models.task import Task
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services import writing_style

# 场景标记行：独占一行，用于 project.script 中区分各集
def episode_marker(index: int, title: str) -> str:
    return f"【第{index}集 {title}】"


OUTLINE_PROMPT = """你是一名资深短剧编剧。根据用户提供的题材设定，规划一部 {episodes} 集短剧的完整分集大纲。

要求输出严格 JSON（不要 markdown 代码块、不要 JSON 之外的任何文字），结构如下：
{{
  "title": "短剧标题(≤20字)",
  "synopsis": "一句话故事梗概(≤50字)",
  "world": "世界观/核心设定(≤200字，供每集保持一致)",
  "episodes": [
    {{"index": 1, "title": "本集标题(≤12字)", "brief": "本集剧情要点(≤120字：发生什么、关键冲突/悬念、结尾钩子)", "characters": ["涉及角色名"], "scenes": ["涉及场景名"]}}
  ]
}}

要求：
- 共 {episodes} 集，集集连贯，构成完整故事弧线（起因→发展→高潮→反转→结局）
- 每集 brief 具体可执行，让编剧能直接按此展开场次与对白
- 每集结尾必须有钩子自然衔接下一集
- 题材类型与用户设定保持一致
- 所有字符串值内禁止使用裸 ASCII 双引号 "，需要引号时用中文引号「」

{style_block}"""


EPISODE_SCRIPT_PROMPT = """你是一名擅长{genre}的短剧编剧。请按集纲撰写第{index}集《{title}》的完整剧本。

【写作要求】
1. 第一行必须是集标记行：{marker}
2. 正文为场次级完整剧本：按剧情拆分 3~8 个场次，每个场次包含：
   - 场景与时间（如「内景·办公室·夜」）
   - 动作/表演描述（角色做什么、表情、走位）
   - 对白（角色名：台词）
   - 旁白（如有，标注「旁白：」）
3. 总字数：单集 5 分钟档 2000~2800 字、10 分钟档 3500~5000 字（简体中文，对白饱满、动作与画面细节充分；正文过短会被判定不合格）
4. 情节紧凑、画面感强、对白口语化短句（单句 ≤20 字），落实本集 brief 中的冲突与钩子
5. 延续前情：角色、伏笔、人物关系必须与前情提要一致，不得跳戏
6. 结尾留下钩子，自然衔接下一集
7. 只输出剧本正文本身，不要任何解释或评论

{style_block}

【短剧核心设定】
{world}

【本集大纲】
标题：{title}
剧情要点：{brief}

【前情提要】
{prev_summary}"""


EPISODE_DRAFT_PROMPT = """你是一名短剧项目统筹。请把给定的一集完整剧本整理为结构化幕（只返回纯 JSON，不要 markdown 代码块、不要任何解释文字）。

输入：第{index}集《{title}》完整剧本。

输出结构：
{{
  "title": "第{index}幕 {title}",
  "synopsis": "本幕剧情概要",
  "segments": [
    {{
      "title": "本镜标题：用 4 个字概括本镜看点/行动（如「林澈拦路」「天台对峙」），禁止出现「画面描述」「镜头」等词，禁止带冒号前缀",
      "shot_type": "远景|全景|中景|近景|特写",
      "camera": "固定|推|拉|摇|移|跟",
      "description": "直接写画面本身（不要带任何前缀标签）：拍什么、视觉细节、出现的角色/场景/道具",
      "dialogue_lines": [
        {{"speaker": "说话角色名", "text": "该镜一句对白", "emotion": "愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"}}
      ],
      "narration": "旁白内容（仅本幕第1镜可填，其余必须为空字符串）",
      "emotion": "本镜整体氛围：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊",
      "duration": 8.0,
      "characters": ["角色名"],
      "scene": "场景名",
      "props": ["道具名"]
    }}
  ],
  "assets": [
    {{"type": "character|scene|prop", "name": "资产名称", "description": "一句话描述（角色写外貌/性别/年龄/服饰，场景写地点/氛围，道具写外观）"}}
  ]
}}

要求：
- 把本集剧本逐场转为分镜，镜头总量对齐全片目标总时长：**目标总时长 {target_total_seconds} 秒，
  平均每镜 7~9 秒、每镜时长硬上限 10 秒，镜头数量 ≈ {target_shot_count} 个（允许 ±15% 浮动），
  各镜 duration 之和 ≈ {target_total_seconds}**；完整覆盖本集全部对白与旁白
- duration 在 5~10 秒内按内容配置（硬顶 10 秒）：动作/情绪重的镜头 9~10s，简短过场 5~6s；
  每镜对白总字数 ≈ duration × 5.5（5s≈28字、10s≈55字），对白超长必须拆成多个连续分镜或调长该镜并拆节拍，
  禁止用超长镜硬扛对白
- **景别偏好（硬约束）**：以中景/近景/人物特写为主（≥80% 镜头）；远景/全景全场最多 1 个且仅作环境交代
  （如开场街道全景）；人物戏一律不得使用远景/全景（H3 远景人脸易糊）
- 旁白仅首镜可填，其余空字符串
- 分镜按 MiniMax H3 视频提示词规范撰写（I2VA：首帧驱动 + 首尾帧衔接），description 必须是包含「画面五要素 + 声音事件」的完整镜头正文，禁止写成摘要句：
  * ①构图：写明前景/中景/背景空间关系与景深层次（如「前景虚化人影，对焦主角中景」），人物/道具位置明确
  * ②光线：写明光源性质与色调（如「黄昏逆光」「霓虹顶光」「夜景蓝调」），禁止空泛「氛围感」
  * ③运镜：写明机位与运动方向/速度（如「缓慢推近至特写」「低角度仰拍、轻微摇镜」），结尾落到结果/反应
  * ④动作时序：用「先…然后…」写清动作顺序与力度/重量感（如「猛地推开门、踉跄后退」），角色形象/服装/配色跨镜保持一致
  * ⑤环境微动态：补 1~2 个动态细节（雾气流动/灯光闪烁/水面反光/衣摆飘动/树叶轻摆等）
  * 声音事件：本镜对白/动作拟音/环境音写入 description 并给出发生的时机感，
    便于拼装幕级 overall_soundscape；情绪必填 emotion
- **上下游分镜衔接（硬约束，适配 I2VA 首尾帧/prev_tail 接续）**：
  * 本镜开端必须延续上一镜结尾：上一镜收在什么表情/姿态/位置/景别/机位，本镜就从哪里开始；
    严禁本镜开端与上一镜尾帧矛盾（如上一镜以「震惊特写」结束，本镜不得从「皱眉」或「远景」另起）。
  * 本镜结尾要自然导向下一镜开端，收在能让下一镜接续的状态（景别/表情/位置兼容）。
  * 运镜与景别连续：若上一镜以特写/近景收尾，本镜运镜应写「保持该景别、轻微推进/跟随」，
    不得写「从远景推近至特写」；只有真正换机位/切景才用大幅运镜（拉远/摇/移）。
  * 情绪/动作连续：本镜开端角色的情绪、动作承接上一镜结尾，不出现情绪断层或突变。
- assets 包含本集用到的全部角色/场景/道具；同名同类只声明一次（与项目已有资产重名会自动复用，无需重复声明）
- 所有字符串值内禁止使用裸 ASCII 双引号 "，需要引号时用中文引号「」
- 分镜 description / 对白不得带 AI 味：禁止模板化起句、三连排比、情绪汇报（如「眼中闪过一丝复杂」）、空泛氛围词
- 只返回 JSON

以下是本集完整剧本（由用户消息提供）："""


def _parse_json(raw: str) -> dict:
    """提取并解析 LLM 输出的 JSON（复用 llm_script_service 的成熟容错：
    兼容 markdown fence、尾随逗号、字符串值内未转义 ASCII 引号）。"""
    from app.services.llm_script_service import _extract_json

    try:
        return _extract_json(raw)
    except Exception:
        return {}


def plan_episode_outline(
    db: Session, brief: str, episodes: int, title: str,
    genre: str | None = None, model_id=None, prior: dict | None = None,
    style_mode: str | None = None,
) -> dict:
    """LLM 规划分集大纲，返回 {title, synopsis, world, episodes}。

    prior：已规划集纲（续写扩集时传入 {title, synopsis, world, episodes}），
    模型严格延续其剧情/角色/世界观，只规划后续 episodes 集。
    """
    if not brief or not brief.strip():
        raise ValueError("题材设定不能为空")
    if episodes < 1 or episodes > 100:
        raise ValueError("集数需在 1~100 之间")
    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    if prior and (prior.get("episodes") or []):
        start_index = len(prior["episodes"]) + 1
        user_content = (
            f"题材设定：{brief.strip()}\n短剧标题：{title}\n类型：{genre or '未指定'}\n"
            f"【已规划集纲】请严格延续以下已规划集数的剧情/角色/世界观/伏笔，"
            f"只规划第 {start_index}~{start_index + episodes - 1} 集（共 {episodes} 集）：\n"
            + json.dumps(prior, ensure_ascii=False)
        )
    else:
        user_content = f"题材设定：{brief.strip()}\n短剧标题：{title}\n类型：{genre or '未指定'}"
    # LLM 偶发输出不完整（JSON 解析失败/集数不足）→ 最多重试 3 次，提升长任务成功率
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = provider.chat(
                [
                    {"role": "system", "content": OUTLINE_PROMPT.format(
                        episodes=episodes,
                        style_block=writing_style.build_inject_block(style_mode, include_anchor=False),
                    )},
                    {"role": "user", "content": user_content},
                ]
            )
            raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            data = _parse_json(raw)
            eps_list = data.get("episodes") or []
            if len(eps_list) < episodes:
                raise ValueError(
                    f"集纲集数不足（收到 {len(eps_list)} 集，期望 {episodes} 集），请重试"
                )
            eps_list = eps_list[:episodes]
            return {
                "title": (data.get("title") or title or "").strip()[:40],
                "synopsis": (data.get("synopsis") or "").strip()[:100],
                "world": (data.get("world") or "").strip()[:500],
                "episodes": [
                    {
                        "index": (start_index + i) if prior and (prior.get("episodes") or []) else i + 1,
                        "title": (c.get("title") or f"第{(start_index + i) if prior and (prior.get('episodes') or []) else i + 1}集").strip()[:30],
                        "brief": (c.get("brief") or "").strip()[:300],
                        "characters": [str(x) for x in (c.get("characters") or []) if x][:10],
                        "scenes": [str(x) for x in (c.get("scenes") or []) if x][:10],
                    }
                    for i, c in enumerate(eps_list)
                ],
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:
                import time
                time.sleep(2)
                continue
            raise
    assert last_err is not None
    raise last_err


def write_episode_script(db: Session, outline: dict, index: int, model_id=None, style_mode: str | None = None) -> str:
    """生成第 index 集场次级完整剧本（index 从 1 开始）。只返回剧本文本，不落库。"""
    episodes = outline.get("episodes") or []
    if index < 1 or index > len(episodes):
        raise ValueError(f"集数 {index} 超出集纲范围（1~{len(episodes)}）")
    ep = episodes[index - 1]

    prev_parts = [
        f"第{c['index']}集《{c['title']}》：{c.get('brief') or ''}"
        for c in episodes[: index - 1]
    ]
    prev_summary = "\n".join(prev_parts[-10:]) or "（本集为开篇集，无前情）"

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    marker = episode_marker(index, ep["title"])
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": EPISODE_SCRIPT_PROMPT.format(
                    genre="现代都市短剧",
                    index=index,
                    title=ep["title"],
                    marker=marker,
                    style_block=writing_style.build_inject_block(style_mode, include_anchor=True),
                    world=outline.get("world") or "",
                    brief=ep.get("brief") or "",
                    prev_summary=prev_summary,
                ),
            },
            {"role": "user", "content": f"请撰写第{index}集《{ep['title']}》的完整剧本。"},
        ]
    )
    text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = text.strip()
    if not text:
        raise ValueError("本集剧本生成内容为空")
    if marker not in text.split("\n")[0]:
        text = marker + "\n\n" + text
    return text


def structure_episode(db: Session, episode_script: str, index: int, title: str, model_id=None, style_mode: str | None = None, target_total_seconds: int = 300) -> dict:
    """把第 index 集场次级剧本整理为 materialize_draft 格式的单幕 JSON。

    返回 {title, synopsis, segments, assets}，供 apply_episode 落库。
    LLM 偶发输出不完整（segments 为空）时自动重试 1 次，提升长任务成功率。
    """
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            model = _resolve_model(db, model_id, ModelType.text, "script")
            provider = ProviderRegistry.for_model_id(db, model.id)
            resp = provider.chat(
                [
                    {
                        "role": "system",
                        "content": EPISODE_DRAFT_PROMPT.format(
                        index=index, title=title,
                        target_total_seconds=str(target_total_seconds),
                        target_shot_count=str(min(70, max(12, int(round(target_total_seconds / 8))))),
                    ),
                    },
                    {"role": "user", "content": episode_script},
                ],
                # 幕 JSON（2~6 分镜 + 对白/旁白/资产）远超 4000 token，不放大预算
                # 会被截断 → segments 为空 → 结构化失败。压低思考把预算让给正文。
                max_tokens=16000,
                suppress_thinking=True,
            )
            raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            data = _parse_json(raw)
            segments = data.get("segments") or []
            # 2026-08-27：景别归一 + 兜底——变体名（大远景/远全景/俯瞰等）先归一到枚举；
            # 远景/全景全场仅保留 1 个（LLM 超出配额的降级中景）；每镜时长硬顶 10s。
            _SHOT_ENUM = ("远景", "全景", "中景", "近景", "特写")
            for _s in segments:
                _v = str(_s.get("shot_type") or "").strip()
                if _v not in _SHOT_ENUM:
                    if "特写" in _v:
                        _s["shot_type"] = "特写"
                    elif "远" in _v or "全" in _v:
                        _s["shot_type"] = "远景"
                    elif "近" in _v:
                        _s["shot_type"] = "近景"
                    elif "中" in _v:
                        _s["shot_type"] = "中景"
                    else:
                        _s["shot_type"] = "中景"
            _overflow = [s for s in segments if s.get("shot_type") in ("远景", "全景")]
            if len(_overflow) > 1:
                keep = _overflow[0]  # 保留第一个作环境交代
                for s in _overflow[1:]:
                    s["shot_type"] = "中景"  # noqa：超出配额的远景/全景降级为中景
            for s in segments:
                try:
                    d = float(s.get("duration") or 8.0)
                except (TypeError, ValueError):
                    d = 8.0
                s["duration"] = min(max(d, 4.0), 10.0)  # 硬顶 10 秒（用户要求每镜 ≤10s）
            if not segments:
                raise ValueError(f"第{index}集剧本结构化失败（未产出分镜），请重试")
            assets = data.get("assets") or []
            return {
                "title": (data.get("title") or f"第{index}幕 {title}").strip()[:200],
                "synopsis": (data.get("synopsis") or "").strip(),
                "segments": segments,
                "assets": assets,
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 0:
                import time
                time.sleep(2)
                continue
            raise
    assert last_err is not None
    raise last_err


def apply_episode(
    db: Session,
    project: Project | None,
    ep_draft: dict,
    episode_script: str,
    index: int,
    per_duration: int = 15,
    project_title: str | None = None,
) -> Project:
    """把一集的结构化幕追加为项目的新幕。

    - project 为空（第 1 集）→ 用 materialize_draft 新建项目（含第 1 幕），
      项目标题用 project_title（短剧标题），不能用幕标题
    - project 已有 → 续接追加（幕号自动 +1），同名资产自动复用
    - 完整剧本（episode_script）逐集追加进 project.script（【第X集】标记分隔）
    返回项目。
    """
    from app.services.llm_script_service import materialize_draft

    draft = {
        "title": project_title or ep_draft.get("title") or "未命名短剧",
        "synopsis": ep_draft.get("synopsis") or "",
        "script": episode_script,
        "assets": ep_draft.get("assets") or [],
        "episodes": [ep_draft],
    }
    p = materialize_draft(
        db, draft,
        synopsis=(ep_draft.get("synopsis") or "")[:500],
        aspect_ratio="9:16",
        per_duration=per_duration,
        existing_project=project,
    )
    # 完整剧本追加：续接模式 materialize_draft 不更新 script，这里手动逐集累积
    if project is not None:
        cur = (project.script or "").strip()
        project.script = (cur + "\n\n" + episode_script.strip()) if cur else episode_script.strip()
        db.commit()
    return p


def load_or_plan_outline(
    db: Session, task: Task, project: Project | None, brief: str, episodes: int,
    title: str, genre: str | None = None, model_id=None,
    style_mode: str | None = None,
) -> dict:
    """读取已存集纲（任务自身 → 项目最近 write_script 任务）或新规划/扩展并存入。

    - 本任务已存集纲（失败重跑断点）→ 直接复用
    - 项目已有关联集纲（续写追加）→ 复用；目标集数超出时扩集（延续已规划剧情）
    - 均无 → 全新规划 episodes 集
    存入 task.provider_task_id 供重跑复用，保证前情一致。
    """
    from app.models.task import TaskType

    if task and task.provider_task_id:
        try:
            existing = json.loads(task.provider_task_id)
            if isinstance(existing, dict) and existing.get("episodes"):
                return existing
        except Exception:
            pass

    existing: dict | None = None
    if project is not None:
        prev = db.scalars(
            select(Task).where(
                Task.type == TaskType.write_script,
                Task.target_id == project.id,
                Task.provider_task_id.isnot(None),
            ).order_by(Task.created_at.desc(), Task.id.desc())
        ).first()
        if prev and (task is None or prev.id != task.id):
            try:
                candidate = json.loads(prev.provider_task_id)
                if isinstance(candidate, dict) and candidate.get("episodes"):
                    existing = candidate
            except Exception:
                existing = None

    if existing and len(existing["episodes"]) >= episodes:
        if task:
            task.provider_task_id = json.dumps(existing, ensure_ascii=False)
            db.commit()
        return existing

    if existing:
        need = episodes - len(existing["episodes"])
        extra = plan_episode_outline(db, brief, need, title, genre, model_id, prior=existing, style_mode=style_mode)
        merged = dict(existing)
        merged["episodes"] = merged["episodes"] + extra["episodes"]
        if task:
            task.provider_task_id = json.dumps(merged, ensure_ascii=False)
            db.commit()
        return merged

    outline = plan_episode_outline(db, brief, episodes, title, genre, model_id, style_mode=style_mode)
    if task:
        task.provider_task_id = json.dumps(outline, ensure_ascii=False)
        db.commit()
    return outline
