"""智能体工作流层 · 创作规划（Plan 工作流）。

从 agent_service.py 剥离（原行号 5782~5920 区域），逻辑未改动。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentSession
from app.services.agent.engine.models import _resolve_chat_model
from app.services.agent.engine.parsing import _parse_json_flexible


def create_plan(db: Session, session_id, message: str, model_id=None) -> "AgentPlan":
    """生成创作规划：LLM 把用户需求拆解为「标题 + 分步规划」，存库返回（status=draft 待确认）。

    规划文档 content 为 Markdown（展示用），steps 为结构化步骤（供前端渲染步骤列表）。
    """
    from app.models.agent import AgentPlan
    from app.providers.registry import ProviderRegistry

    session = db.get(AgentSession, session_id)
    if not session:
        raise ValueError("会话不存在")
    message = (message or "").strip()
    if not message:
        raise ValueError("规划需求不能为空")
    model = _resolve_chat_model(db, model_id)
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是创作规划师（短剧行业）。把用户的需求拆解为可执行的创作规划。"
                    "必须输出严格 JSON，格式如下（不要输出 JSON 之外的任何文字）：\n"
                    '{"title": "规划标题(≤20字)", "content": "完整规划文档（Markdown，含总目标、分阶段说明、产出物、验收要点）", '
                    '"steps": [{"title": "步骤1标题", "description": "该步骤要完成什么、产出什么"}]}\n'
                    "要求：steps 拆 5-8 步，覆盖故事大纲→角色→分集→关键场景/对白→视觉风格→验收；"
                    "description 要具体可执行，能让另一个 AI 直接按描述干活。"
                ),
            },
            {"role": "user", "content": message},
        ]
    )
    raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse_json_flexible(raw)
    if not data or not data.get("title") or not isinstance(data.get("steps"), list):
        raise ValueError("规划生成失败，请重试")
    steps = [
        {
            "title": (s.get("title") or "步骤").strip(),
            "description": (s.get("description") or "").strip(),
            "status": "pending",
        }
        for s in data["steps"]
        if s.get("title") or s.get("description")
    ]
    if not steps:
        raise ValueError("规划生成失败，请重试")
    plan = AgentPlan(
        session_id=session.id,
        title=(data.get("title") or "创作规划")[:200],
        content=data.get("content") or "",
        steps=steps,
        status="draft",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def get_plan(db: Session, plan_id) -> "AgentPlan":
    from app.models.agent import AgentPlan

    plan = db.get(AgentPlan, plan_id)
    if not plan:
        raise ValueError("规划不存在")
    return plan


def list_plans(db: Session, session_id) -> list:
    from app.models.agent import AgentPlan

    return list(
        db.scalars(
            select(AgentPlan)
            .where(AgentPlan.session_id == session_id)
            .order_by(AgentPlan.created_at.asc())
        ).all()
    )


def confirm_plan(db: Session, plan_id) -> "AgentPlan":
    """确认规划：status draft → confirmed，进入可执行状态。"""
    from app.models.agent import AgentPlan

    plan = db.get(AgentPlan, plan_id)
    if not plan:
        raise ValueError("规划不存在")
    if plan.status == "done":
        raise ValueError("规划已完成，无法再确认")
    plan.status = "confirmed"
    db.commit()
    db.refresh(plan)
    return plan


def mark_plan_step_done(db: Session, plan_id, step_index: int) -> "AgentPlan":
    """标记规划某一步为 done；全部完成后整份规划 status → done。"""
    import copy

    from app.models.agent import AgentPlan

    plan = db.get(AgentPlan, plan_id)
    if not plan:
        raise ValueError("规划不存在")
    steps = copy.deepcopy(plan.steps or [])
    if not (0 <= step_index < len(steps)):
        raise ValueError("步骤不存在")
    steps[step_index]["status"] = "done"
    plan.steps = steps
    if all(s.get("status") == "done" for s in steps):
        plan.status = "done"
    db.commit()
    db.refresh(plan)
    return plan


def delete_plan(db: Session, plan_id) -> bool:
    from app.models.agent import AgentPlan

    plan = db.get(AgentPlan, plan_id)
    if not plan:
        return False
    db.delete(plan)
    db.commit()
    return True
