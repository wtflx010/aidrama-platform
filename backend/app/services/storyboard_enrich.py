"""分镜复用后处理：五要素补缺。

2026-08-27：三阶段确认流里，generate_shot_preview 优先复用智能体在对话中写好的分镜
（解析直落）。为兼顾「快」与「要素完整」，解析后对每镜做轻量五要素检查（构图/光线/
运镜/动作），缺项用单次批量 LLM 只补缺失部分——不改写已有内容、不整镜重写，
避免退回一次性整篇重构（80 秒+）。对白不列为必填（纯画面镜本就没对白）。
"""
from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)

# 视觉要素 × 触发词（命中任一词即视为该要素已覆盖，避免重复补写）
# 电影感标准：在基础四要素外加 质感/景深/气氛，让补缺产物更接近精品宣传片观感。
_VISUAL = {
    "构图": ("构图", "前景", "中景", "背景", "站位", "居中", "空间", "左右", "前后", "景深", "虚化"),
    "光线": ("光", "灯", "明暗", "色调", "逆光", "顶光", "侧光", "霓虹", "暖黄", "冷白", "黄昏", "阴影", "夜灯"),
    "运镜": ("推", "拉", "摇", "移", "跟", "俯拍", "仰拍", "镜头", "机位", "特写", "环绕", "平移"),
    "动作": ("走", "坐", "站", "推", "抓", "抬", "转", "低", "伸", "攥", "笑", "哭", "握", "抱", "抖", "动作", "脚步", "起身", "扑", "挡"),
    "质感": ("质感", "纹理", "皮肤", "布料", "褶皱", "材质", "颗粒", "水渍", "金属", "粗糙"),
    "景深": ("景深", "浅景深", "对焦", "焦点", "前景虚", "背景虚", "层层"),
    "气氛": ("氛围", "雾", "烟", "雨", "风", "衣摆", "树叶", "水波", "尘埃", "灯光闪", "压抑", "清冷", "暖意", "呼吸"),
}


def _covered(text: str) -> set:
    t = text or ''
    return {k for k, words in _VISUAL.items() if any(w in t for w in words)}


def missing_elements(segment: dict) -> list:
    text = "\n".join((segment.get("description") or "", segment.get("title") or ""))
    have = _covered(text)
    return [k for k in _VISUAL if k not in have]


def enrich_direct_episodes(db, episodes) -> int:
    """批量补缺所有缺要素的分镜（就地追加到 description），返回补了多少镜。失败静默跳过。"""
    targets = [{"seg": s, "missing": missing_elements(s)}
               for ep in episodes for s in (ep.get('segments') or []) if missing_elements(s)]
    if not targets:
        return 0
    try:
        fills = _batch_fill(db, targets)
    except Exception as e:  # noqa: BLE001
        logger.warning("分镜要素补缺失败（保留原文）: %s", e)
        return 0
    n = 0
    for t in targets:
        add = fills.get((t["seg"].get("title") or "").strip())
        if add:
            cur = (t["seg"].get("description") or "").strip()
            t["seg"]["description"] = cur + ("\n" + add if cur else add)
            n += 1
    return n


def _batch_fill(db, targets) -> dict:
    """一次批量 LLM 调用：只输出每个分镜缺失要素的补充句。"""
    from app.models.model_config import ModelType
    from app.providers.registry import ProviderRegistry
    from app.services.keyframe_service import _resolve_model
    from app.services.llm_script_service import _extract_json

    model = _resolve_model(db, None, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    lines = []
    for t in targets:
        seg = t["seg"]
        missing = "、".join(t["missing"])
        cur = (seg.get("description") or "").strip() or "（当前无画面描述）"
        dlg = "；".join((d.get("speaker") or "") + "：" + (d.get("text") or "") for d in (seg.get("dialogue_lines") or []))
        lines.append("- 分镜 " + str(seg.get("title")) + "，缺失要素：" + missing + "；现有描述：" + cur + ("；对白：" + dlg if dlg else ""))
    sys = "你是短剧分镜补写助手。只补充每个分镜缺失的要素（每项一句、20~35 字、具体可拍、偏电影质感），不得改写已有描述、不得增删剧情与对白、不得整镜重写，只输出 JSON。"
    usr = "请仅针对每个分镜缺失的要素各补一句中文（画面语言），输出 JSON：" + json.dumps({"fills": [{"no": "分镜编号", "add": "光线：…\n运镜：…"}]}, ensure_ascii=False) + "\n\n" + "\n".join(lines)
    resp = provider.chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}])
    content = resp["choices"][0]["message"]["content"] or ""
    data = _extract_json(content)
    out = {}
    for f in (data.get('fills') or []):
        no = str(f.get("no") or "").strip()
        add = (f.get("add") or "").strip()
        if no and add:
            out[no] = add
    return out