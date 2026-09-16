"""创作对接层 · 项目：会话剧本 → 结构化项目草案 → 落库 / 删除（确认流）。

从 agent_service.py 剥离（原行号 3698~3926 区域），逻辑未改动。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.providers.registry import ProviderRegistry
from app.services.agent.creative.subagent import (
    _collect_creative_state_context,
    _collect_script_context,
)

logger = logging.getLogger(__name__)

_PROJECT_DRAFT_PROMPT = """你是一名短剧项目统筹。根据对话中已产出的剧本创作内容，整理为结构化短剧项目草案（只返回纯 JSON，不要 markdown 代码块、不要任何解释文字）。

要求：
- 必须基于提供的内容整理，不要凭空新增剧情/角色/场景/道具/镜头；用户已确认过的内容务必全部保留
- 若同一创作内容存在多个版本（多次「重新生成/重写/修改」产生的新旧剧本并存），一律以标记为【最新版，以此为准】的内容为准；【较早版本】仅作参考，严禁混用或选用旧版本
- 若用户最近的消息明确指定使用某一版本（如「用第一个/第二个剧本」「按最新版」「按上一版」），以用户的明确指定为准
- 结构如下：
{{
  "title": "短剧标题，10字以内",
  "synopsis": "一句话故事梗概",
  "script": "完整剧本文本（包含全部对白与旁白，内容不足时省略）",
  "assets": [
    {{"type": "character|scene|prop", "name": "资产名称", "description": "一句话描述（角色写外貌/性别/年龄/服饰，场景写地点/氛围，道具写外观）"}}
  ],
  "episodes": [
    {{
      "title": "第N幕 标题",
      "synopsis": "本幕剧情概要",
      "segments": [
        {{
          "title": "本镜标题：用 4 个字概括本镜看点/行动（如「林澈拦路」「天台对峙」），禁止出现「画面描述」「镜头」等词，禁止带冒号前缀",
          "shot_type": "远景|全景|中景|近景|特写",
          "camera": "固定|推|拉|摇|移|跟",
          "shot_beats": "可选：镜头内有明确时间分节（动作/情绪/机位变化）时填本镜「分镜内多镜头运镜节拍」——每项 {{"start_sec": 秒, "end_sec": 秒, "shot_type": "景别", "camera": "运镜", "content": "该时段画面：写具体动作动词（如：猛地推门/攥拳抬头）"}}，时间连续无缝覆盖 0~duration（首拍 0 起、末拍止于 duration），拍数 2~3；无明确分节填 []；范例：[{{"start_sec": 0.0, "end_sec": 2.0, "shot_type": "近景", "camera": "推", "content": "主角推门而入"}}, {{"start_sec": 2.0, "end_sec": 4.0, "shot_type": "中景", "camera": "摇", "content": "与对方对视"}}, {{"start_sec": 4.0, "end_sec": 6.0, "shot_type": "全景", "camera": "拉", "content": "落座桌边"}}]"
          "description": "直接写画面本身（不要带任何前缀标签）：写清楚拍什么、视觉细节、角色/场景/道具；动作化写作——有动作的镜头写 ≥2 个具体动词（推门/攥拳/抬头/踉跄）按时序展开并给力度重量感（猛地/缓缓），运镜写「类型+幅度+速度」，禁止情绪形容词替代动作；纯静止气氛镜补环境微动态（衣摆/发丝/光线）",
          "dialogue_lines": [
            {{"kind": "dialogue|inner", "speaker": "说话角色名", "text": "台词原文", "emotion": "愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"}}
          ],
          "narration": "旁白内容（每幕第1镜可填，其余必须为空字符串）",
          "emotion": "本镜整体氛围：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊",
          "duration": 8.0,
          "characters": ["角色名"],
          "scene": "场景名",
          "props": ["道具名"]
        }}
      ]
    }}
  ]
}}
- 幕数与剧本中的分集/幕数保持一致：剧本有几集（或几幕）就输出几幕，完整覆盖剧本全部剧情，严禁压缩、合并、删减幕；若剧本未明确分幕，按剧情自然段落（起承转合/高光场景）拆分
- 每幕分镜 4~10 个（以剧情关键节点为核心，尽量完整保留原剧本的剧情节拍与动作细节）；当幕数较多（≥6 幕）时每幕保留 3~6 个核心分镜，优先保证幕数与剧情完整；duration 由你根据镜头内容在 1~15 秒内自动配置（建议整数）：镜头信息量越大、动作/情绪越重时长越长（如 8~15s），简短过场可短（如 5~6s），不要再把所有分镜写成相同时长
- 对白口语化（单句 ≤30 字），每镜对白总字数 ≈ duration × 9（5s≈45字、10s≈90字、15s≈135字），对白超长时拆成多个连续分镜或调长该镜 duration
- **上下游分镜衔接（硬约束，适配首尾帧/尾帧接续）**：同幕内相邻分镜必须连贯——本镜开端延续上一镜结尾（表情/姿态/位置/景别/机位一致，如上一镜以「震惊特写」结束，本镜不得从「皱眉」或「远景」另起）；本镜结尾自然导向下一镜开端；运镜与景别连续（若上一镜特写收尾，本镜写「保持该景别、轻微推进/跟随」，不得写「从远景推近至特写」，只有真正换机位/切景才用大幅运镜）；开端情绪/动作承接上一镜结尾，不出现情绪断层
- assets 中所有角色/场景/道具必须在 segments 中被引用，反之 segments 引用的名称必须在 assets 中存在；同名同类只声明一次
- 如果提供的内容不足以支撑结构化（无剧本信息），返回 {{"title": "<项目名>", "synopsis": "", "assets": [], "episodes": []}}
- 所有字符串值内禁止使用裸 ASCII 双引号 "，需要引号时用中文引号「」
- 只返回 JSON

对话创作内容：
{context}

用户要求的项目名：{title}"""


def _build_project_draft(db: Session, session_id, title_hint: str, model) -> dict:
    """把会话中已产出的剧本内容整理为结构化项目草案（materialize_draft 格式）。

    空内容防护（2026-08-17）：LLM 整理产出空壳（无资产/无幕）时**不再静默回退空项目**
    落库——先重试一次（附「必须产出完整结构」指令），仍无效则抛错让工具失败，
    由模型/用户感知问题，避免此前「创建成功但项目是空壳」的掩埋式失败。
    会话中确实没有任何剧本内容时，返回最小空草案（无内容可整理，属预期路径）。
    """
    from app.services.llm_script_service import _extract_json

    # P1 状态层：优先读创作状态卡（各角色当前版本，单一事实来源）；
    # 无状态卡数据（如剧本直接在主对话产出）时回退到聊天历史收集。
    context = _collect_creative_state_context(db, session_id) or _collect_script_context(db, session_id)
    if not (context or "").strip():
        # 会话没有任何剧本/创作内容：确属「无内容可整理」，返回最小草案（不重试不报错）
        return {"title": title_hint or "未命名短剧", "synopsis": "", "assets": [], "episodes": []}

    provider = ProviderRegistry.for_model(model)

    def _call(prompt_extra: str = "") -> dict:
        content = _PROJECT_DRAFT_PROMPT.format(
            context=context or "（对话中暂无剧本内容）",
            title=title_hint,
        )
        if prompt_extra:
            content += "\n\n" + prompt_extra
        # 结构化完整草案常远超 4000 token（分镜/对白 JSON 较长），务必放大输出
        # 预算并压低思考，避免输出被 max_tokens 截断导致 JSON 缺 } 而解析失败。
        resp = provider.chat(
            [{"role": "user", "content": content}],
            max_tokens=16000,
            suppress_thinking=True,
        )
        raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return _extract_json(raw)

    def _is_empty(d: dict) -> bool:
        return not (d.get("assets") or []) and not (d.get("episodes") or [])

    draft = _call()
    if draft and not _is_empty(draft):
        return draft
    # 第一次产出为空壳（有内容却整理失败）：带「必须产出完整结构」重试一次
    logger.warning("项目草案为空壳（有 %d 字剧本内容），重试一次", len(context))
    retry = _call(
        "注意：对话中已有剧本/创作内容，请务必从中整理出 assets 与 episodes "
        "（至少 1 个角色或场景资产、至少 1 幕），不要返回空结构！",
    )
    if retry and not _is_empty(retry):
        return retry
    raise ValueError(
        "项目内容整理失败：会话中已有剧本内容，但模型两次都未能整理出结构化项目。"
        "请重试，或先让我把剧本写得更完整再创建项目。"
    )


def _tool_create_project_draft(db: Session, session_id, args: dict, model) -> dict:
    """生成项目草案（不创建项目）：读取会话剧本内容 → LLM 结构化 → 返回 pending 确认载荷。

    载荷结构：
    {
      "draft": 结构化草案（title/synopsis/script/assets/episodes[segments]）,
      "summary": "N 幕 / X 角色 / Y 场景 / Z 道具 / M 分镜",
      "session_id": str,
      "title": 项目名,
      "default": {"aspect_ratio": "...", "style_id": null, "art_style_prompt": null},
    }
    """
    title = (args.get("title") or "").strip()
    if not title:
        raise ValueError("项目名称不能为空")
    draft = _build_project_draft(db, session_id, title, model)
    assets = draft.get("assets") or []
    eps = draft.get("episodes") or []
    chars = sum(1 for a in assets if a.get("type") == "character")
    scenes = sum(1 for a in assets if a.get("type") == "scene")
    props = sum(1 for a in assets if a.get("type") == "prop")
    segs = sum(len(ep.get("segments") or []) for ep in eps)
    summary = f"{len(eps)} 幕 / {chars} 角色 / {scenes} 场景 / {props} 道具 / {segs} 分镜"
    ratio = (args.get("aspect_ratio") or "").strip() or "9:16"
    if ratio not in ("16:9", "9:16", "1:1", "4:3", "3:4"):
        ratio = "9:16"
    return {
        "draft": draft,
        "summary": summary,
        "session_id": str(session_id),
        "title": title,
        "default": {"aspect_ratio": ratio, "style_id": None, "art_style_prompt": None},
    }


def confirm_project(
    db: Session, message_id, aspect_ratio: str = "9:16",
    style_id=None, art_style_prompt: str | None = None,
    resolution: str | None = None, video_params: dict | None = None,
) -> "Project":
    """确认创建项目：读取 pending 草案 → materialize_draft 落库完整项目（资产+分镜）→ 更新工具消息。

    前端确认卡确认后调用；返回已创建的 Project。
    resolution / video_params：项目详情可设置的视频参数（批量生成按项目级出片时的参数源）。
    """
    from app.models.agent import AgentMessage
    from app.models.project import Project
    from app.services.llm_script_service import materialize_draft

    msg = db.get(AgentMessage, message_id)
    if not msg or msg.role != "tool" or msg.tool_name != "create_project":
        raise ValueError("项目草案不存在或已失效，请重新让智能体生成项目")
    payload = ((msg.tool_params or {}).get("pending_project_draft")) if msg.tool_params else None
    if not payload:
        raise ValueError("项目草案不存在或已失效，请重新让智能体生成项目")
    draft = dict(payload.get("draft") or {})
    if not draft.get("title"):
        draft["title"] = payload.get("title") or "未命名短剧"
    ratio = aspect_ratio or "9:16"
    if ratio not in ("16:9", "9:16", "1:1", "4:3", "3:4"):
        ratio = "9:16"
    try:
        project = materialize_draft(
            db, draft,
            synopsis=(draft.get("synopsis") or "")[:500],
            aspect_ratio=ratio,
            resolution=resolution or "720p",
            style_id=style_id,
            art_style_prompt=art_style_prompt,
            per_duration=15,
            video_params=video_params or {},
        )
    except Exception:
        db.rollback()
        raise
    # 更新工具消息为已完成，关联项目
    msg.tool_status = "succeeded"
    msg.project_id = project.id
    msg.content = f"项目「{project.title}」已创建（id={project.id}）"
    tp = dict(msg.tool_params or {})
    tp["project_id"] = str(project.id)
    msg.tool_params = tp
    db.commit()
    return project


def project_delete_confirm(db: Session, message_id) -> dict:
    """确认删除项目：读取 pending 待删载荷 → 真正删除（含幕/分镜/资产/视频/音频及磁盘文件）→ 更新工具消息。

    前端删除确认卡确认后调用；返回 {title, detail} 供前端提示。
    """
    from app.models.agent import AgentMessage
    from app.services.project_service import delete as _delete_project

    msg = db.get(AgentMessage, message_id)
    if not msg or msg.role != "tool" or msg.tool_name != "project_delete":
        raise ValueError("项目删除确认不存在或已失效，请重新让智能体选择项目删除")
    payload = ((msg.tool_params or {}).get("pending_project_delete")) if msg.tool_params else None
    if not payload:
        raise ValueError("项目删除确认不存在或已失效，请重新让智能体选择项目删除")
    pid = str(payload.get("project_id") or "").strip()
    title = str(payload.get("title") or "").strip() or "该项目"
    if not pid:
        raise ValueError("项目删除信息缺失，请重新让智能体选择项目删除")
    from uuid import UUID as _UUID
    try:
        pid_uuid = _UUID(pid)
    except Exception:
        raise ValueError(f"项目 id 无效：{pid}")  # noqa: B904
    ok = _delete_project(db, pid_uuid)
    if not ok:
        raise ValueError("项目删除失败，请稍后重试")
    # 更新工具消息为已完成（保留确认载荷，前端回放显示已删除状态）
    msg.tool_status = "succeeded"
    msg.content = f"项目「{title}」已删除"
    tp = dict(msg.tool_params or {})
    tp["project_deleted"] = True
    msg.tool_params = tp
    db.commit()
    return {"title": title, "detail": str(payload.get("detail") or "")}
