"""画布生成方案体系（2026-08-29）：统一「选择方案 → 按协议执行 → 回写」入口。

每个方案 = 注册表一项 SchemeSpec：元数据（菜单展示 + 前端 input_kind 决定渲染哪个输入收集器）
+ generate_fn（后端执行，返回 (Task, node_ids, extra)）。
新增方案 = 追加注册项 + 其执行函数（+ 自带的 TaskType/回写协议），画布菜单自动出现。
"""
from typing import Callable

from app.models.task import Task  # noqa: F401  re-export for 调用方类型标注


class SchemeSpec:

    def __init__(
        self,
        *,
        key: str,
        label: str,
        description: str = "",
        input_kind: str = "node-batch",
        generate_fn: Callable,
    ):
        self.key = key
        self.label = label
        self.description = description
        self.input_kind = input_kind
        self.generate_fn = generate_fn

    def meta(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "input_kind": self.input_kind,
        }

    def __repr__(self) -> str:
        return f"<Scheme {self.key}>"


# ── 方案实现 ────────────────────────────────────────────────────────


def _single_node(db, board_id, payload) -> tuple[Task, list[str], list[dict]]:
    """single：单镜直出（逐节点 image/video），对齐既有 M1 generate 行为。"""
    from app.schemas.canvas import CanvasBoardGenerate
    from app.services import canvas_service

    typed = CanvasBoardGenerate(
        node_ids=payload.node_ids,
        model_id=payload.model_id,
        ratio=payload.ratio,
        kind=payload.kind or "image",
    )
    task, results = canvas_service.generate(db, board_id, typed)
    node_ids = [r.get("node_id") for r in results if r.get("node_id")]
    return task, node_ids, results



def _director_text(db, board_id, payload) -> tuple[Task, list[str], list[dict]]:
    """director_text：多段连拍·纯文生（t2v）——同一导演台执行器、不同参数组合。"""
    from app.schemas.canvas import DirectorGenerate
    from app.services import canvas_director_service

    cfg = dict(payload.config or {})
    cfg["task_type"] = "t2v — 文生视频(Text to Video)"
    cfg["enable_common_refs"] = False
    typed = DirectorGenerate(node_ids=payload.node_ids, shots=payload.shots, config=cfg)
    task, node_ids, total_frames = canvas_director_service.generate(db, board_id, typed)
    return task, node_ids, [{"message": f"{len(node_ids)} 段 · {total_frames} 帧（t2v 纯文生）"}]

def _director(db, board_id, payload) -> tuple[Task, list[str], list[dict]]:
    """director：多段连续生视频整片（对齐既有导演台模式）。"""
    from app.schemas.canvas import DirectorGenerate
    from app.services import canvas_director_service

    typed = DirectorGenerate(
        node_ids=payload.node_ids,
        shots=payload.shots,
        config=payload.config,
    )
    task, node_ids, total_frames = canvas_director_service.generate(db, board_id, typed)
    message = f"{len(node_ids)} 段 · {total_frames} 帧" if total_frames else ""
    return task, node_ids, [{"message": message}]


# ── 注册表 ────────────────────────────────────────────────────────

SCHEMES: dict[str, SchemeSpec] = {
    "single": SchemeSpec(
        key="single",
        label="单镜直出",
        description="逐节点生成关键帧/镜头视频（R2V 直出）：选择分镜节点后逐镜出片，产物回挂节点。适用于单镜打磨与批量产片。",
        input_kind="node-batch",
        generate_fn=_single_node,
    ),
    "director_text": SchemeSpec(
        key="director_text",
        label="多段连拍·纯文生",
        description="无需资产参考图，仅用每段提示词连续生成整片（t2v）：适合无角色/场景资产的脑暴稿与节奏试片。",
        input_kind="director-segments",
        generate_fn=_director_text,
    ),
    "director": SchemeSpec(
        key="director",
        label="多段连拍·整片",
        description="把一组分镜串成一段连续影片一次出片：分段脚本 + 段间引导，产物为整片视频节点。适用于多镜连拍、长片段连续生成。",
        input_kind="director-segments",
        generate_fn=_director,
    ),
}


def list_schemes() -> list[dict]:
    """方案元数据列表（GET /canvas/schemes）。按注册顺序返回。"""
    return [spec.meta() for spec in SCHEMES.values()]


def generate(db, board_id, payload):
    """统一执行入口：按 payload.scheme 分发。返回 (task, node_ids, extra_nodes)。"""
    spec = SCHEMES.get(payload.scheme)
    if spec is None:
        keys = ", ".join(SCHEMES.keys())
        raise ValueError(f"未知方案「{payload.scheme}」；可用: {keys}")
    task, node_ids, extra = spec.generate_fn(db, board_id, payload)
    return task, node_ids, extra
