"""动作序列业务服务（白模故事版与多镜头动作展示，2026-08-10）。

链路（手动触发，幕级看板「生成动作预览」按钮，不自动触发）：
1. generate_template：聚合该幕下 action_sequence=key 的连续分镜 → LLM 输出
   格子总数 + 每格动作节拍 + 格子分组（N 格一组 = 一个 5s 镜头）→ 建 ActionSequence 行
   → 派发 generate_action_sequence_template 任务（生成一张多格白模模板图）
2. compose：任务内把模板图按 grid 布局裁剪成格子图 → 逐组派发 generate_action_video
   子任务（格子图 R2V 姿态强参考 + 正式资产外观）→ 轮询 → 硬切拼接成完整动作视频
   → 回写 composed_url（export_film 幕级段优先用它，实现「替换幕级片段」）
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action_sequence import ActionSequence
from app.models.asset import Asset
from app.models.media import MediaStatus
from app.models.model_config import ModelType
from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services.llm_script_service import _extract_json

logger = logging.getLogger(__name__)

# 格子分组 LLM 模板：读动作分镜 → 格数 + 每格节拍 + 分组
_GROUP_TMPL = """你是动作戏导演兼 AI 分镜设计师。下面是一场动作戏的连续分镜（按镜头顺序排列），
需要把它转换成一张「多格动作模板图」的格子布局方案，供 AI 图生图生成白模（灰模）分镜表。

【动作分镜信息】（JSON，按 index 顺序）
{segments_json}

【资产设定】（角色/场景/道具，格子画面必须沿用，不得引入无关资产）
{assets_block}

【规则】
1. grid_count：格子总数，按动作复杂程度与格数习惯取 6/9/12/16（3 的倍数优先），
   每格代表一个**动作节拍**（如：起手/蓄力/逼近/交锋/变招/格挡/反击/收势…），
   格子从左到右、从上到下按时间顺序排布。
2. cells：grid_count 个格子，每格给 index（1 起递增）、beat（动作节拍名）、
   pose（该格角色的动作姿态描述，灰模摆姿）、shot_type、camera。
3. groups：把这些格子按动作节拍分组——**N 格一组 = 一个 5 秒视频镜头**。
   - 动作连贯快速（交锋/追击）时多格并组（2~4 格一组），表现连续动作；
   - 动作有明显停顿/关键格（起手、收势）可 1 格一组；
   - 组之间不得跳格、不得重叠，全部格子恰好分完；
   - 每组对应原分镜中的一个或多个连续镜头：segment_indexes 填该组覆盖的分镜 index
     （必须覆盖全部输入分镜、不重叠）；一组可覆盖多个连续分镜（动作连贯时）。
   - 每组给 shot_type / camera（该 5s 镜头的景别运镜）与 prompt（英文，该组动作的
     5 秒连贯动作描述，含姿态/动作速度/镜头运动，供视频生成使用）。
4. 白模模板图只表达动作姿态与镜头构图，不写台词文字；prompt 不得出现文字/字幕。

要求返回**纯 JSON**（不要 markdown 代码块、不要任何解释文字），结构：
{{
  "grid_count": 9,
  "layout": "3x3",
  "cells": [
    {{"index": 1, "beat": "起手", "pose": "青年男子躬身跨步，双掌提至腰侧蓄力", "shot_type": "全景", "camera": "固定"}}
  ],
  "groups": [
    {{"id": 1, "cells": [1, 2, 3], "segment_indexes": [1], "shot_type": "全景", "camera": "跟", "prompt": "a young man lunges forward, throwing a palm strike, camera tracks his movement, fast agile motion"}}
  ]
}}
- cells 数量必须等于 grid_count；groups 的 cells 恰好覆盖 1..grid_count 不重叠
- groups 的 segment_indexes 恰好覆盖全部输入分镜不重叠
- 所有字符串值内禁止使用裸 ASCII 双引号 "；需要引号时用中文引号「」
- 只返回 JSON"""


def collect_sequence_segments(db: Session, episode_id, sequence_key: str) -> list[Segment]:
    """聚合该幕下 action_sequence=key 的连续分镜（按 index 排序）。"""
    return list(
        db.scalars(
            select(Segment)
            .where(
                Segment.episode_id == episode_id,
                Segment.action_sequence == sequence_key,
            )
            .order_by(Segment.index.asc())
        ).all()
    )


def list_sequence_keys(db: Session, episode_id) -> list[str]:
    """该幕出现过的动作序列标记（去重、按分镜顺序）。"""
    rows = db.scalars(
        select(Segment.action_sequence)
        .where(Segment.episode_id == episode_id, Segment.action_sequence.isnot(None))
        .order_by(Segment.index.asc())
    ).all()
    seen: list[str] = []
    for k in rows:
        if k and k not in seen:
            seen.append(k)
    return seen


def _asset_brief(db: Session, segments: list[Segment]) -> str:
    """收集动作段涉及的资产设定（角色/场景/道具）供 LLM 分组参考。"""
    char_ids: set[str] = set()
    scene_id = None
    prop_ids: set[str] = set()
    for s in segments:
        for cid in s.character_ids or []:
            char_ids.add(str(cid))
        if s.scene_id:
            scene_id = str(s.scene_id)
        for pid in s.prop_ids or []:
            prop_ids.add(str(pid))

    def _get(aid) -> Asset | None:
        try:
            from uuid import UUID

            return db.get(Asset, UUID(aid))
        except (ValueError, TypeError):
            return None

    lines: list[str] = []
    for cid in char_ids:
        a = _get(cid)
        if a:
            lines.append(f"角色「{a.name}」：{a.expanded_description or a.description or a.name}")
    if scene_id:
        a = _get(scene_id)
        if a:
            lines.append(f"场景「{a.name}」：{a.expanded_description or a.description or a.name}")
    for pid in prop_ids:
        a = _get(pid)
        if a:
            lines.append(f"道具「{a.name}」：{a.expanded_description or a.description or a.name}")
    return "\n".join(lines) if lines else "- （无资产上下文）"


def plan_template_groups(
    db: Session, segments: list[Segment], *, model_id=None,
) -> dict:
    """LLM 输出格子分组方案：{grid_count, layout, cells, groups}。

    失败/输出不合格时回退 _fallback_template_groups（不阻断）。
    """
    payload = [
        {
            "index": s.index,
            "shot_type": s.shot_type,
            "camera": s.camera,
            "emotion": s.emotion,
            "duration": s.duration,
            "description": (s.description or "")[:200],
        }
        for s in segments
    ]
    prompt = _GROUP_TMPL.format(
        segments_json=json.dumps(payload, ensure_ascii=False),
        assets_block=_asset_brief(db, segments),
    )
    try:
        model = _resolve_model(db, model_id, ModelType.text, "storyboard")
        provider = ProviderRegistry.for_model_id(db, model.id)
        resp = provider.chat([{"role": "user", "content": prompt}])
        content = resp["choices"][0]["message"]["content"]
        data = _extract_json(content)
    except Exception as e:
        logger.warning("[action_seq] LLM 格子分组失败，回退均分：%s", map_to_chinese(e))
        return _fallback_template_groups(segments)

    plan = _validate_plan(data, segments)
    if plan is None:
        logger.warning("[action_seq] LLM 格子分组输出不合格，回退均分")
        return _fallback_template_groups(segments)
    return plan


def _validate_plan(data: dict, segments: list[Segment]) -> dict | None:
    """校验 LLM 分组：cells 数量=grid_count、groups 覆盖 1..grid_count、segment_indexes 覆盖全部分镜。"""
    if not isinstance(data, dict):
        return None
    try:
        grid_count = int(data.get("grid_count") or 0)
    except (TypeError, ValueError):
        return None
    cells = [c for c in (data.get("cells") or []) if isinstance(c, dict)]
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)]
    if grid_count <= 0 or len(cells) != grid_count or not groups:
        return None

    cell_indexes = [int(c.get("index")) for c in cells if c.get("index") is not None]
    if sorted(cell_indexes) != list(range(1, grid_count + 1)):
        return None

    # groups 覆盖格子 + 分镜，不重叠
    covered_cells: set[int] = set()
    covered_segs: set[int] = set()
    valid_groups: list[dict] = []
    for g in groups:
        try:
            g_cells = sorted(int(c) for c in (g.get("cells") or []))
            g_segs = sorted(int(i) for i in (g.get("segment_indexes") or []))
        except (TypeError, ValueError):
            return None
        if not g_cells or not g_segs:
            return None
        if any(c < 1 or c > grid_count or c in covered_cells for c in g_cells):
            return None
        if any(i not in {s.index for s in segments} or i in covered_segs for i in g_segs):
            return None
        covered_cells.update(g_cells)
        covered_segs.update(g_segs)
        valid_groups.append({
            "cells": g_cells,
            "segment_indexes": g_segs,
            "shot_type": (g.get("shot_type") or "").strip(),
            "camera": (g.get("camera") or "").strip(),
            "prompt": (g.get("prompt") or "").strip(),
        })
    if (
        covered_cells != set(range(1, grid_count + 1))
        or covered_segs != {s.index for s in segments}
        or not valid_groups
    ):
        return None
    return {
        "grid_count": grid_count,
        "layout": (data.get("layout") or "").strip(),
        "cells": [
            {
                "index": int(c.get("index")),
                "beat": (c.get("beat") or "").strip(),
                "pose": (c.get("pose") or "").strip(),
                "shot_type": (c.get("shot_type") or "").strip(),
                "camera": (c.get("camera") or "").strip(),
            }
            for c in cells
        ],
        "groups": valid_groups,
    }


def _fallback_template_groups(segments: list[Segment]) -> dict:
    """兜底分组：每镜 2 格（起手/完成），一格一组 → 每镜一个 5s 视频。"""
    cells: list[dict] = []
    groups: list[dict] = []
    idx = 0
    for s in segments:
        desc = (s.description or f"分镜{s.index}").strip()
        for k, beat in ((1, "动作起始"), (2, "动作完成")):
            idx += 1
            cells.append({
                "index": idx,
                "beat": beat,
                "pose": desc[:60],
                "shot_type": s.shot_type or "全景",
                "camera": s.camera or "固定",
            })
        groups.append({
            "cells": [idx - 1, idx],
            "segment_indexes": [s.index],
            "shot_type": s.shot_type or "全景",
            "camera": s.camera or "固定",
            "prompt": f"{desc[:200]} 连贯动作，镜头稳定跟随，动作自然流畅",
        })
    return {
        "grid_count": len(cells),
        "layout": "",
        "cells": cells,
        "groups": groups,
    }


def _grid_layout(grid_count: int) -> tuple[int, int]:
    """格子布局 rows×cols（接近方形，时序从左到右、从上到下）。"""
    cols = int(__import__("math").ceil(grid_count**0.5))
    rows = int(__import__("math").ceil(grid_count / cols))
    return rows, cols


def list_by_episode(db: Session, episode_id) -> list[ActionSequence]:
    return list(
        db.scalars(
            select(ActionSequence)
            .where(ActionSequence.episode_id == episode_id)
            .order_by(ActionSequence.created_at.asc())
        ).all()
    )


def generate_template(db: Session, episode_id, sequence_key: str, *, model_id=None):
    """动作序列阶段1 入口（手动触发）：聚合分镜 → 建 ActionSequence → 派发模板图任务。

    返回 (sequence, task, segments)。已有同名序列则先删除（重新生成）。
    """
    from app.tasks.generate_action_sequence import generate_action_sequence_template_task

    ep = db.get(Episode, episode_id)
    if not ep:
        raise ValueError("幕不存在")
    segments = collect_sequence_segments(db, episode_id, sequence_key)
    if not segments:
        raise ValueError(f"幕内没有标记为「{sequence_key}」的动作分镜")
    if len(segments) < 2:
        raise ValueError(f"动作序列「{sequence_key}」只有 {len(segments)} 个分镜，至少需 2 个连续动作分镜")

    # 重新生成：删除旧序列（含未完成任务）
    for old in list_by_episode(db, episode_id):
        if old.sequence_key == sequence_key:
            if old.status == MediaStatus.running:
                raise ValueError("该动作序列正在生成中，请等待完成后再试")
            db.delete(old)
    db.flush()

    model = _resolve_model(db, model_id, ModelType.image, "img2img")
    seq = ActionSequence(
        episode_id=ep.id,
        sequence_key=sequence_key,
        segment_ids=[str(s.id) for s in segments],
        status=MediaStatus.pending,
    )
    db.add(seq)
    db.flush()

    task = Task(
        project_id=ep.project_id,
        type=TaskType.generate_action_sequence_template,
        target_type="action_sequence",
        target_id=seq.id,
        model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    db.commit()
    db.refresh(seq)
    db.refresh(task)
    generate_action_sequence_template_task.delay(str(task.id))
    logger.info(
        "[action_seq] 幕 %s 动作序列 %s 已派发模板图任务 task=%s 分镜=%d",
        episode_id, sequence_key, task.id, len(segments),
    )
    return seq, task, segments
