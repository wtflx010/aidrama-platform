"""关键帧参考图解析测试。

测试 _select_view_for_shot（四视图智能选择）和 _resolve_ref_urls（道具/角色/场景参考图）。
用 MagicMock 模拟 db.get(Asset, uuid)。
"""
import uuid
from unittest.mock import MagicMock
from types import SimpleNamespace

from app.services.keyframe_service import _select_view_for_shot, _resolve_ref_urls


def _make_asset(
    *,
    name="林浅",
    type="character",
    cover_url="https://example.com/cover.png",
    four_view_urls=None,
    expanded_description=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        type=type,
        cover_url=cover_url,
        four_view_urls=four_view_urls or [],
        expanded_description=expanded_description,
    )


def _make_payload(use_reference=True, ref_image_urls=None):
    return SimpleNamespace(
        use_reference=use_reference,
        ref_image_urls=ref_image_urls,
    )


def _make_segment(
    *,
    character_ids=None,
    scene_id=None,
    prop_ids=None,
    shot_type=None,
    camera=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        index=1,
        character_ids=character_ids or [],
        scene_id=scene_id,
        prop_ids=prop_ids or [],
        shot_type=shot_type,
        camera=camera,
    )


def _mock_db_get(asset_map: dict):
    """创建 mock db，db.get(Asset, uuid) 按 asset_map 返回。"""
    db = MagicMock()

    def get(model, oid):
        return asset_map.get(str(oid))

    db.get.side_effect = get
    db.scalar.return_value = None  # 默认无上一镜关键帧（P6 链式关闭），链式测试用 side_effect 覆盖
    return db


# ─── _select_view_for_shot：四视图智能选择 ──────────────────────────
# P7.9 起 four_view_urls=[正面, 侧面, 背面, 特写]；特写/近景→特写[3]，中景/全景/远景→正面[0]

def test_select_view_closeup_uses_closeup_view():
    """特写 → 特写视图 [3]。"""
    asset = _make_asset(four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    assert _select_view_for_shot(asset, "特写", None) == "closeup.png"


def test_select_view_wide_uses_front():
    """远景 → 正面视图 [0]（正面面板即全身构图）。"""
    asset = _make_asset(four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    assert _select_view_for_shot(asset, "远景", None) == "front.png"


def test_select_view_pan_uses_side():
    """摇镜头 → 侧面视图 [1]（运镜覆盖景别）。"""
    asset = _make_asset(four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    assert _select_view_for_shot(asset, "近景", "摇") == "side.png"


def test_select_view_no_fourview_falls_back_to_cover():
    """无四视图 → 回退封面。"""
    asset = _make_asset(four_view_urls=[])
    assert _select_view_for_shot(asset, "特写", None) == "https://example.com/cover.png"


def test_select_view_unknown_shot_defaults_to_front():
    """未知景别 → 默认正面 [0]。"""
    asset = _make_asset(four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    assert _select_view_for_shot(asset, "未知景别", None) == "front.png"


# ─── _resolve_ref_urls：道具/角色/场景参考图 ───────────────────────

def test_resolve_refs_includes_prop_cover():
    """道具 cover_url 应纳入参考图（之前被忽略）。"""
    prop = _make_asset(name="古剑", type="prop", cover_url="https://example.com/sword.png")
    seg = _make_segment(prop_ids=[str(prop.id)])
    db = _mock_db_get({str(prop.id): prop})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert len(refs) >= 1
    # 道具封面应在参考图中（可能是主图或辅助参考）
    assert "sword.png" in refs[0] or any("sword.png" in r for r in refs)


def test_resolve_refs_single_character_no_stitch():
    """单角色 → 直接返回该角色视图，不拼接。"""
    char = _make_asset(
        name="林浅",
        four_view_urls=["front.png", "side.png", "back.png", "closeup.png"],
    )
    seg = _make_segment(character_ids=[str(char.id)], shot_type="特写")
    db = _mock_db_get({str(char.id): char})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert len(refs) >= 1
    assert refs[0] == "closeup.png"  # 特写→特写[3]，单角色不拼接


def test_resolve_refs_medium_shot_uses_character_primary():
    """中景（人物主体/对白戏）→ 角色图作主参考，场景辅助，防止场景封面压过人物。"""
    char = _make_asset(name="苏尘", four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    scene = _make_asset(name="苏家族宴厅", type="scene", cover_url="https://example.com/hall.png")
    seg = _make_segment(
        character_ids=[str(char.id)],
        scene_id=str(scene.id),
        shot_type="中景",
    )
    db = _mock_db_get({str(char.id): char, str(scene.id): scene})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    # 中景 → 正面视图（_SHOT_TYPE_VIEW_INDEX[中景]=0）作主参考，场景作辅助
    assert refs[0] == "front.png"
    assert any("hall.png" in r for r in refs[1:])  # 场景作辅助


def test_resolve_refs_wide_shot_character_primary():
    """全景/远景（有角色）→ 角色图作主参考，场景辅助（2026-08-07：角色优先锚定长相）。"""
    char = _make_asset(name="苏尘", four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    scene = _make_asset(name="祖宅回廊", type="scene", cover_url="https://example.com/corridor.png")
    seg = _make_segment(
        character_ids=[str(char.id)],
        scene_id=str(scene.id),
        shot_type="全景",
    )
    db = _mock_db_get({str(char.id): char, str(scene.id): scene})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    # 有角色 → 角色正面视图作主参考，场景辅助
    assert "front.png" in refs[0]
    assert any("corridor.png" in r for r in refs[1:])  # 场景辅助


def test_resolve_refs_character_scene_prop_all_referenced():
    """多图生关键帧（2026-08-07）：角色+场景+道具全部作为参考传入，超 3 张截断。"""
    char = _make_asset(name="苏尘", four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    scene = _make_asset(name="祖宅回廊", type="scene", cover_url="https://example.com/corridor.png")
    prop1 = _make_asset(name="古剑", type="prop", cover_url="https://example.com/sword.png")
    prop2 = _make_asset(name="玉佩", type="prop", cover_url="https://example.com/jade.png")
    seg = _make_segment(
        character_ids=[str(char.id)],
        scene_id=str(scene.id),
        prop_ids=[str(prop1.id), str(prop2.id)],
        shot_type="中景",
    )
    db = _mock_db_get({
        str(char.id): char, str(scene.id): scene,
        str(prop1.id): prop1, str(prop2.id): prop2,
    })

    refs = _resolve_ref_urls(db, seg, _make_payload())
    # 角色作主图 + 场景 + 道具，最多 4 张（FLUX.2 Klein 官方参考图上限）
    assert refs[0] == "front.png"
    assert len(refs) <= 4
    assert any("corridor.png" in r for r in refs)  # 场景在参考中
    assert any("sword.png" in r or "jade.png" in r for r in refs)  # 道具在参考中
    assert any("sword.png" in r for r in refs) or any("jade.png" in r for r in refs)


# ─── P6 关键帧链式：上一镜关键帧作主参考 ──────────────────────────

def test_resolve_refs_chain_prev_keyframe_primary():
    """链式：角色特写作主参考（refs[0]），上一镜关键帧作辅助放末尾（refs[-1]）。

    2026-08-08 用户拍板：所选资产（角色/场景/道具）优先作主参考，prev_kf 不再占
    主参考位（此前 prev_kf 画面主导会覆盖用户所选资产形象）。
    """
    char = _make_asset(name="苏尘", four_view_urls=["front.png", "side.png", "back.png", "closeup.png"])
    seg = _make_segment(character_ids=[str(char.id)], shot_type="近景")
    db = _mock_db_get({str(char.id): char})
    prev_seg = SimpleNamespace(id=uuid.uuid4())
    prev_kf = SimpleNamespace(image_url="https://example.com/kf_prev.png")
    db.scalar.side_effect = [prev_seg, prev_kf]  # 上一镜 + 其关键帧

    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert refs[0] == "closeup.png"  # 角色特写作主参考
    assert refs[-1] == "https://example.com/kf_prev.png"  # 链式参考放末尾辅助


def test_resolve_refs_chain_prev_without_kf_falls_back():
    """上一镜存在但无成功关键帧 → 回退现有逻辑（无链式）。"""
    seg = _make_segment(character_ids=[], shot_type="全景")
    db = _mock_db_get({})
    db.scalar.side_effect = [SimpleNamespace(id=uuid.uuid4()), None]
    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert refs == []


def test_resolve_refs_chain_first_segment_no_prev():
    """幕首镜（无上一镜）→ 无链式，回退现有逻辑。"""
    seg = _make_segment(character_ids=[], shot_type="全景")
    db = _mock_db_get({})
    db.scalar.return_value = None
    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert refs == []


def test_scene_cover_prompt_excludes_people():
    """场景封面 prompt/负面词必须排除人物（防止参考图带人污染关键帧）。"""
    from app.tasks.generate_asset_cover import _TYPE_PREFIX, _TYPE_NEGATIVE
    from app.models.asset import AssetType

    assert "no people" in _TYPE_PREFIX[AssetType.scene]
    assert "no characters" in _TYPE_PREFIX[AssetType.scene]
    assert "person" in _TYPE_NEGATIVE[AssetType.scene]
    assert "people" in _TYPE_NEGATIVE[AssetType.scene]


def test_resolve_refs_scene_as_primary_when_no_character():
    """无角色时场景封面作为主图。"""
    scene = _make_asset(name="咖啡馆", type="scene", cover_url="https://example.com/cafe.png")
    seg = _make_segment(scene_id=str(scene.id))
    db = _mock_db_get({str(scene.id): scene})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert len(refs) >= 1
    assert "cafe.png" in refs[0]


def test_resolve_refs_no_assets_returns_empty():
    """无任何资产 → 返回空列表（回退纯文生图）。"""
    seg = _make_segment()
    db = _mock_db_get({})

    refs = _resolve_ref_urls(db, seg, _make_payload())
    assert refs == []


def test_resolve_refs_use_reference_false_returns_empty():
    """use_reference=False → 返回空。"""
    char = _make_asset(four_view_urls=["front.png"])
    seg = _make_segment(character_ids=[str(char.id)])
    db = _mock_db_get({str(char.id): char})

    refs = _resolve_ref_urls(db, seg, _make_payload(use_reference=False))
    assert refs == []


def test_resolve_refs_explicit_ref_image_urls_passthrough():
    """显式指定 ref_image_urls → 原样返回。"""
    seg = _make_segment()
    db = _mock_db_get({})
    explicit = ["https://example.com/custom.png"]

    refs = _resolve_ref_urls(db, seg, _make_payload(ref_image_urls=explicit))
    assert refs == explicit
