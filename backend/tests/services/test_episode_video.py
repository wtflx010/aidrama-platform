"""幕级视频（P7.6）服务测试：LLM 分幕规划、真实拆幕、一幕一视频、幕级叙事 prompt、设计图规划、导出收集。"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import episode_video_service as evs
from app.services import export_service


def _seg(index: int, duration: float = 4.0, dialogue: str | None = None,
         narration: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"s{index}", episode_id="ep1", index=index, shot_type="中景",
        camera="固定", description=f"分镜{index}：人物在场景中行动",
        duration=duration, emotion="平静",
        dialogue_lines=([{"speaker": "甲", "text": dialogue}] if dialogue else []),
        narration=narration,
    )


def _script(splits: list[dict] | None = None, per_duration: int = 15) -> dict:
    return {
        "per_duration": per_duration,
        "scene": "江南雨巷夜景，青石板路湿亮",
        "characters": "苏尘：青年男子，素白长衫外罩青灰氅衣",
        "light": "暮蓝冷调，冷光自左前方45度斜射入",
        "style": "Art style: 写实电影风格",
        "splits": splits if splits is not None else [
            {"index": 1, "title": "主幕", "shot_indexes": [1, 2],
             "narrative": "苏尘撑伞缓步走来，雨水沿伞沿滴落",
             "first_scene": "雨巷全景，苏尘撑伞身影出现在巷口",
             "last_scene": "苏尘抬眼望向巷口深处"},
        ],
    }


def _group(segs, narrative="画面连贯推进", first_scene="起始画面", last_scene="结束画面") -> dict:
    return {"segments": segs, "narrative": narrative, "first_scene": first_scene, "last_scene": last_scene}


class _ScalarResult:
    """模拟 db.scalars(...) 返回（支持 .all()）。"""

    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


# ─── build_segment_prompt（P7.2 幕级连贯叙事）────────────────────────


def test_segment_prompt_structure():
    script = _script()
    group = _group(
        [_seg(1, 4.0, dialogue="前路漫漫"), _seg(2, 4.0, narration="他停下脚步。")],
        narrative="苏尘撑着油纸伞自巷口缓步走来，雨水沿伞沿滴落，他抬眼穿过雨幕望向巷口深处",
    )
    prompt = evs.build_segment_prompt(script, group)

    assert prompt.startswith("【场景】")
    assert "江南雨巷夜景，青石板路湿亮" in prompt
    assert "【角色】" in prompt
    assert "苏尘：青年男子，素白长衫外罩青灰氅衣" in prompt
    assert "【光线】" in prompt
    assert "暮蓝冷调，冷光自左前方45度斜射入" in prompt
    assert "【剧情】" in prompt
    assert "苏尘撑着油纸伞自巷口缓步走来" in prompt
    assert "【台词原句】" in prompt
    assert "甲用中文说：「前路漫漫」" in prompt
    assert "旁白（画外音" in prompt
    assert "【画质约束】" in prompt
    assert "Art style: 写实电影风格" in prompt
    # P7.2：不得出现「第X镜」时间轴标记
    assert "第1镜" not in prompt and "时间轴" not in prompt
    assert "禁止文字、字幕、水印" in prompt


def test_segment_prompt_without_speech():
    script = _script()
    group = _group([_seg(1, 4.0)])
    prompt = evs.build_segment_prompt(script, group)
    assert "【台词原句】" not in prompt
    assert "【语言要求】" not in prompt
    assert "【剧情】" in prompt


def test_segment_prompt_falls_back_to_defaults():
    # script 缺全局设定、group 缺 narrative → 程序默认值
    script = {}
    group = _group([_seg(1, 4.0)], narrative="")
    prompt = evs.build_segment_prompt(script, group)
    assert "连续场景，风格统一" in prompt
    assert "角色特征不变" in prompt
    assert "光影一致" in prompt


# ─── target_episode_count / plan_groups（分幕，P7.6）────────────────


def test_target_episode_count_within_duration():
    segs = [_seg(i, 4.0) for i in range(1, 4)]  # 12s ≤ 15s → 1 幕
    assert evs.target_episode_count(segs, 15) == 1


def test_target_episode_count_over_duration():
    segs = [_seg(i, 4.0) for i in range(1, 7)]  # 24s → ceil(24/15)=2 幕
    assert evs.target_episode_count(segs, 15) == 2


def test_target_episode_count_heavy_over_duration():
    segs = [_seg(i, 6.0) for i in range(1, 7)]  # 36s → ceil(36/18)=2 幕
    assert evs.target_episode_count(segs, 18) == 2


def test_plan_groups_uses_llm_narrative_split():
    segs = [_seg(i, 4.0) for i in range(1, 7)]  # 6 镜 24s → 目标 2 幕
    script = _script(splits=[
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "幕1叙事",
         "first_scene": "幕1起始", "last_scene": "幕1结束"},
        {"index": 2, "title": "主幕-2", "shot_indexes": [4, 5, 6], "narrative": "幕2叙事",
         "first_scene": "幕2起始", "last_scene": "幕2结束"},
    ])
    groups = evs.plan_groups(segs, script, 15)
    assert len(groups) == 2
    assert [s.index for s in groups[0]["segments"]] == [1, 2, 3]
    assert [s.index for s in groups[1]["segments"]] == [4, 5, 6]
    assert groups[0]["narrative"] == "幕1叙事" and groups[1]["narrative"] == "幕2叙事"
    assert groups[0]["first_scene"] == "幕1起始" and groups[1]["last_scene"] == "幕2结束"


def test_plan_groups_falls_back_when_not_full_coverage():
    segs = [_seg(i, 4.0) for i in range(1, 7)]
    script = _script(splits=[  # 只覆盖 1-3，漏 4-6
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "幕1",
         "first_scene": "f1", "last_scene": "l1"},
    ])
    groups = evs.plan_groups(segs, script, 15)
    # 回退均分 2 幕（24s → 目标 2 幕，每幕 3 镜）
    assert len(groups) == 2
    assert [s.index for s in groups[0]["segments"]] == [1, 2, 3]
    assert [s.index for s in groups[1]["segments"]] == [4, 5, 6]


def test_plan_groups_single_episode_when_within_duration():
    segs = [_seg(i, 4.0) for i in range(1, 4)]  # 12s ≤ 15s → 1 幕
    script = _script(splits=[
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "整幕叙事",
         "first_scene": "起始", "last_scene": "结束"},
    ])
    groups = evs.plan_groups(segs, script, 15)
    assert len(groups) == 1
    assert [s.index for s in groups[0]["segments"]] == [1, 2, 3]


def test_plan_groups_ignores_wrong_episode_count():
    # LLM 切 3 幕但目标只要 2 幕 → 回退均分 2 幕
    segs = [_seg(i, 4.0) for i in range(1, 7)]
    script = _script(splits=[
        {"index": 1, "title": "幕1", "shot_indexes": [1, 2], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
        {"index": 2, "title": "幕2", "shot_indexes": [3, 4], "narrative": "幕2",
         "first_scene": "f", "last_scene": "l"},
        {"index": 3, "title": "幕3", "shot_indexes": [5, 6], "narrative": "幕3",
         "first_scene": "f", "last_scene": "l"},
    ])
    groups = evs.plan_groups(segs, script, 15)
    assert len(groups) == 2
    assert [s.index for s in groups[0]["segments"]] == [1, 2, 3]
    assert [s.index for s in groups[1]["segments"]] == [4, 5, 6]


def test_plan_groups_rejects_split_over_duration():
    # 某幕分镜时长总和超过目标时长 → 该幕非法，回退均分
    segs = [_seg(i, 4.0) for i in range(1, 7)]
    script = _script(splits=[
        {"index": 1, "title": "幕1", "shot_indexes": [1, 2, 3, 4], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
        {"index": 2, "title": "幕2", "shot_indexes": [5, 6], "narrative": "幕2",
         "first_scene": "f", "last_scene": "l"},
    ])
    groups = evs.plan_groups(segs, script, 15)
    # 幕1 16s > 15s → LLM 分幕非法 → 回退均分
    assert len(groups) == 2
    assert [s.index for s in groups[0]["segments"]] == [1, 2, 3]
    assert [s.index for s in groups[1]["segments"]] == [4, 5, 6]


# ─── _splits_valid（预写 video_script 复用校验，P7.6）──────────────


def test_splits_valid_accepts_prewritten_single_split():
    # 小说改编预写的 video_script（单段覆盖全部分镜、时长 ≤ 目标）→ 可直接复用
    segs = [_seg(i, 4.0) for i in range(1, 4)]  # 12s ≤ 15s
    script = _script(splits=[
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
    ], per_duration=15)
    assert evs._splits_valid(script, segs, 15) is True


def test_splits_valid_rejects_missing_splits():
    segs = [_seg(i, 4.0) for i in range(1, 4)]
    assert evs._splits_valid({}, segs, 15) is False
    assert evs._splits_valid({"per_duration": 15, "splits": []}, segs, 15) is False


def test_splits_valid_rejects_wrong_per_duration():
    segs = [_seg(i, 4.0) for i in range(1, 4)]
    script = _script(splits=[
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
    ], per_duration=15)
    assert evs._splits_valid(script, segs, 18) is False


def test_splits_valid_rejects_split_over_duration():
    segs = [_seg(i, 4.0) for i in range(1, 6)]  # 20s，4 镜一组超 15s
    script = _script(splits=[
        {"index": 1, "title": "幕1", "shot_indexes": [1, 2, 3, 4], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
        {"index": 2, "title": "幕2", "shot_indexes": [5], "narrative": "幕2",
         "first_scene": "f", "last_scene": "l"},
    ], per_duration=15)
    assert evs._splits_valid(script, segs, 15) is False  # 幕1 16s > 15s


def test_splits_valid_rejects_gap_or_overlap():
    segs = [_seg(i, 4.0) for i in range(1, 4)]
    # 漏掉 3
    script = _script(splits=[
        {"index": 1, "title": "幕1", "shot_indexes": [1, 2], "narrative": "幕1",
         "first_scene": "f", "last_scene": "l"},
    ], per_duration=15)
    assert evs._splits_valid(script, segs, 15) is False


# ─── build_episode_videos（一幕一视频，P7.6）───────────────────────


def test_build_episode_videos_one_row_per_episode():
    db = MagicMock()
    ep1_segs = [_seg(i, 4.0) for i in range(1, 4)]  # 12s → 1 幕
    ep2_segs = [_seg(i, 4.0) for i in range(4, 7)]
    db.scalars.side_effect = [_ScalarResult(ep1_segs), _ScalarResult(ep2_segs)]
    ep1 = SimpleNamespace(id="ep1", video_script=json.dumps(_script(splits=[
        {"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3], "narrative": "幕1叙事",
         "first_scene": "f1", "last_scene": "l1"},
    ]), ensure_ascii=False))
    ep2 = SimpleNamespace(id="ep2", video_script=json.dumps(_script(splits=[
        {"index": 1, "title": "主幕-2", "shot_indexes": [4, 5, 6], "narrative": "幕2叙事",
         "first_scene": "f2", "last_scene": "l2"},
    ]), ensure_ascii=False))
    rows = evs.build_episode_videos(db, [ep1, ep2], SimpleNamespace(id="m1"), 1280, 720, 15)

    # P7.6：每幕 1 行，index=1
    assert len(rows) == 2
    r1, r2 = rows
    assert r1.index == 1 and r2.index == 1
    # P7.2：首/尾帧不在此处解析（由生成任务阶段1 回填幕级设计图）
    assert r1.first_frame_url is None and r1.last_frame_url is None
    assert r1.num_frames == 361 and r1.model_id == "m1"  # 15s → 361 帧
    # 幕 prompt 用幕级叙事，无「第X镜」
    assert "幕1叙事" in r1.prompt and "幕2叙事" in r2.prompt
    assert "第1镜" not in r1.prompt and "第4镜" not in r2.prompt


# ─── plan_design_images（幕级设计图集，P7.2）────────────────────────


def test_plan_design_images_single_segment():
    groups = [_group([_seg(1, 4.0), _seg(2, 4.0)],
                     first_scene="幕首画面", last_scene="幕尾画面")]
    designs = evs.plan_design_images(groups, _script())
    # 1 段 → 2 张：段1首图（幕首图）+ 段1尾图（幕尾图）
    assert [d["key"] for d in designs] == ["seg1_first", "seg1_last"]
    assert designs[0]["is_opening"] and not designs[0]["is_closing"]
    assert designs[1]["is_closing"] and not designs[1]["is_opening"]
    assert designs[0]["scene_desc"] == "幕首画面"
    assert designs[1]["scene_desc"] == "幕尾画面"


def test_plan_design_images_multi_segment():
    groups = [
        _group([_seg(1), _seg(2), _seg(3), _seg(4)], first_scene="段1起始", last_scene="段1结束"),
        _group([_seg(5), _seg(6)], first_scene="段2起始", last_scene="段2结束"),
    ]
    designs = evs.plan_design_images(groups, _script())
    # 2 段 → 3 张：段1首（幕首）、段2首（衔接=段1尾=段2首）、段2尾（幕尾）
    assert [d["key"] for d in designs] == ["seg1_first", "seg2_first", "seg2_last"]
    assert designs[0]["is_opening"] is True
    assert designs[1]["is_opening"] is False and designs[1]["is_closing"] is False
    assert designs[2]["is_closing"] is True
    assert designs[0]["scene_desc"] == "段1起始"
    assert designs[1]["scene_desc"] == "段2起始"
    assert designs[2]["scene_desc"] == "段2结束"


def test_plan_design_images_falls_back_to_description():
    groups = [_group([_seg(1)], first_scene="", last_scene="")]
    designs = evs.plan_design_images(groups, _script())
    # first_scene 为空 → 回退首镜分镜描述
    assert designs[0]["scene_desc"] == "分镜1：人物在场景中行动"


def test_build_design_image_prompt():
    p = evs.build_design_image_prompt("雨巷全景，苏尘撑伞身影出现在巷口", "写实电影风格")
    assert "雨巷全景，苏尘撑伞身影出现在巷口" in p
    assert "电影级画面" in p
    assert "Art style: 写实电影风格" in p


# ─── _resolve_design_refs（设计图参考图，P7.1/2026-08-07 修复）────────


def test_resolve_design_refs_caps_at_six_and_uses_cover():
    """多图合成必崩：首帧/衔接图参考收敛为 场景+首个角色 ≤2 张（不再塞全部角色/道具）。"""
    import uuid as _uuid
    db = MagicMock()

    def _asset(cover, views=4):
        return SimpleNamespace(
            name=cover, cover_url=f"http://x/{cover}.png",
            four_view_urls=[f"http://x/{cover}_v{i}.png" for i in range(views)],
        )

    sc, c1, c2, c3, p1 = (_uuid.uuid4() for _ in range(5))
    assets = {
        str(sc): _asset("scene", views=0),
        str(c1): _asset("c1"), str(c2): _asset("c2"), str(c3): _asset("c3"),
        str(p1): _asset("p1", views=0),
    }

    def _get(model, oid):
        return assets.get(str(oid))
    db.get.side_effect = _get

    seg = SimpleNamespace(
        scene_id=sc,
        character_ids=[c1, c2, c3],
        prop_ids=[p1],
    )
    refs = evs._resolve_design_refs(db, seg)
    # 场景 + 首个角色（主角），配角/道具不参与 → 避免多图融合崩坏（morphing/手部畸形）
    assert refs == ["http://x/scene.png", "http://x/c1.png"]
    assert all("_v" not in r for r in refs)  # 无任何四视图分图混入


def test_resolve_design_refs_no_assets_returns_empty():
    """无任何资产 → 空列表（任务回退纯文生图）。"""
    import uuid as _uuid
    db = MagicMock()
    db.get.return_value = None
    sc, c1, p1 = (_uuid.uuid4() for _ in range(3))
    seg = SimpleNamespace(scene_id=sc, character_ids=[c1], prop_ids=[p1])
    assert evs._resolve_design_refs(db, seg) == []


def test_resolve_design_refs_scene_first_then_characters():
    """单角色 + 场景：场景在前、主角 cover 在后。"""
    import uuid as _uuid
    db = MagicMock()
    sc, c1 = _uuid.uuid4(), _uuid.uuid4()
    db.get.side_effect = lambda model, oid: {
        str(sc): SimpleNamespace(cover_url="http://x/scene.png", four_view_urls=[]),
        str(c1): SimpleNamespace(cover_url="http://x/c1.png", four_view_urls=[]),
    }.get(str(oid))
    seg = SimpleNamespace(scene_id=sc, character_ids=[c1], prop_ids=[])
    refs = evs._resolve_design_refs(db, seg)
    assert refs == ["http://x/scene.png", "http://x/c1.png"]


def test_resolve_design_refs_core_only_closing_frame():
    """尾帧（core_only）：只锚定首个角色（主角），不带场景/配角/道具参考。

    修复首尾帧雷同：场景 cover（如族宴厅全景）会把尾帧强拉回首帧的
    "全员同框"，core_only 只留主角外形，画面跟随 prompt 收束。
    """
    import uuid as _uuid
    db = MagicMock()
    sc, lead, extra, prop = (_uuid.uuid4() for _ in range(4))
    db.get.side_effect = lambda model, oid: {
        str(sc): SimpleNamespace(cover_url="http://x/scene.png", four_view_urls=[]),
        str(lead): SimpleNamespace(cover_url="http://x/lead.png", four_view_urls=[]),
        str(extra): SimpleNamespace(cover_url="http://x/extra.png", four_view_urls=[]),
        str(prop): SimpleNamespace(cover_url="http://x/prop.png", four_view_urls=[]),
    }.get(str(oid))
    seg = SimpleNamespace(scene_id=sc, character_ids=[lead, extra], prop_ids=[prop])

    refs = evs._resolve_design_refs(db, seg, core_only=True)
    # 仅主角；场景/配角/道具均不参与尾帧（避免首尾帧雷同）
    assert refs == ["http://x/lead.png"]

    # 非 core_only：场景 + 首个角色（多图合成崩坏的修复③：不再全量收集）
    full = evs._resolve_design_refs(db, seg)
    assert full == ["http://x/scene.png", "http://x/lead.png"]


# ─── enhance_design_prompt（设计图 LLM 增强，P7.4）──────────────────


def test_enhance_design_prompt_success(monkeypatch):
    db = MagicMock()
    db.get.return_value = SimpleNamespace(
        name="苏尘", description="青年男子，素白长衫",
        expanded_description=None,
    )
    seg = _seg(1)
    seg.character_ids = ["c1"]
    seg.scene_id = "sc1"
    seg.prop_ids = []
    valid_prompt = (
        "Su Chen standing in a rainy alley at night, blue tones, holding an oil-paper umbrella, "
        "highly detailed, sharp focus, shallow depth of field, cinematic lighting, "
        "subtle film grain, 8k uhd, Art style: 写实电影风格"
    )

    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(
        {"prompt": valid_prompt, "negative_prompt": "low quality, blurry"}
    )}}]}
    monkeypatch.setattr(evs.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr("app.services.prompt_enhance_service._resolve_text_model", lambda *a, **k: SimpleNamespace(id="m1"))

    prompt, neg = evs.enhance_design_prompt(
        db, "雨巷全景，苏尘撑伞身影", seg, "写实电影风格",
        narrative="段剧情", shot_type="中景", role="幕首图",
    )
    assert prompt == valid_prompt and neg == "low quality, blurry"


def test_enhance_design_prompt_falls_back_on_llm_failure(monkeypatch):
    db = MagicMock()
    seg = _seg(1)
    seg.character_ids = []
    seg.scene_id = None
    seg.prop_ids = []

    provider = MagicMock()
    provider.chat.side_effect = RuntimeError("LLM 挂了")
    monkeypatch.setattr(evs.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr("app.services.prompt_enhance_service._resolve_text_model", lambda *a, **k: SimpleNamespace(id="m1"))

    prompt, neg = evs.enhance_design_prompt(db, "雨巷全景", seg, None)
    assert "雨巷全景" in prompt
    assert "low quality" in neg  # 兜底负面词


def test_enhance_design_prompt_core_only_keeps_only_lead(monkeypatch):
    """尾帧（core_only=True）：资产块只含主角，场景/配角/道具不进入 prompt。"""
    db = MagicMock()
    assets = {
        "lead": SimpleNamespace(name="苏尘", description="主角", expanded_description=None),
        "extra": SimpleNamespace(name="二房族人", description="配角", expanded_description=None),
        "sc1": SimpleNamespace(name="族宴厅", description="场景", expanded_description=None),
        "p1": SimpleNamespace(name="灵牌", description="道具", expanded_description=None),
    }
    db.get.side_effect = lambda model, oid: assets.get(str(oid))
    seg = _seg(1)
    seg.character_ids = ["lead", "extra"]
    seg.scene_id = "sc1"
    seg.prop_ids = ["p1"]

    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(
        {"prompt": "valid prompt Art style: 写实电影风格 highly detailed", "negative_prompt": "low quality"}
    )}}]}
    monkeypatch.setattr(evs.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr("app.services.prompt_enhance_service._resolve_text_model", lambda *a, **k: SimpleNamespace(id="m1"))
    captured = {}

    def _chat(messages):
        captured["content"] = messages[0]["content"]
        return provider.chat.return_value
    provider.chat.side_effect = _chat

    evs.enhance_design_prompt(
        db, "苏尘离场背影", seg, "写实电影风格",
        narrative="段剧情", shot_type="中景", role="幕尾图",
        core_only=True,
    )
    content = captured["content"]
    assert "苏尘" in content and "二房族人" not in content
    assert "族宴厅" not in content and "灵牌" not in content  # 场景/道具不写入资产块


def test_enhance_design_prompt_non_core_only_keeps_scene_and_lead(monkeypatch):
    """首帧/衔接图（core_only=False）：资产块只含场景 + 首个角色，配角/道具不写入。

    多图合成崩坏修复：prompt 不写满配角外貌（模型无参考会硬画崩），群像交给模型自由生成。
    """
    import uuid as _uuid
    db = MagicMock()
    lead, extra, sc, prop = (_uuid.uuid4() for _ in range(4))
    assets = {
        str(lead): SimpleNamespace(name="苏尘", description="主角", expanded_description=None),
        str(extra): SimpleNamespace(name="二房族人", description="配角", expanded_description=None),
        str(sc): SimpleNamespace(name="族宴厅", description="场景", expanded_description=None),
        str(prop): SimpleNamespace(name="灵牌", description="道具", expanded_description=None),
    }
    db.get.side_effect = lambda model, oid: assets.get(str(oid))
    seg = _seg(1)
    seg.character_ids = [lead, extra]
    seg.scene_id = sc
    seg.prop_ids = [prop]

    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(
        {"prompt": "valid prompt Art style: 写实电影风格 highly detailed", "negative_prompt": "low quality"}
    )}}]}
    monkeypatch.setattr(evs.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr("app.services.prompt_enhance_service._resolve_text_model", lambda *a, **k: SimpleNamespace(id="m1"))
    captured = {}

    def _chat(messages):
        captured["content"] = messages[0]["content"]
        return provider.chat.return_value
    provider.chat.side_effect = _chat

    evs.enhance_design_prompt(
        db, "族宴厅全景", seg, "写实电影风格",
        narrative="段剧情", shot_type="中景", role="幕首图",
    )
    content = captured["content"]
    assert "苏尘" in content and "族宴厅" in content
    assert "二房族人" not in content and "灵牌" not in content  # 配角/道具不写入资产块


# ─── plan_video_script（LLM 分幕规划，P7.6）────────────────────────


def _mock_plan_env(monkeypatch, content: str, raise_exc: bool = False):
    ep = SimpleNamespace(
        id="ep1",
        project=SimpleNamespace(style_id=None, art_style_prompt=None),
        video_script=None,
    )
    db = MagicMock()
    db.get.return_value = ep
    db.scalars.return_value = _ScalarResult([_seg(i, 4.0) for i in range(1, 3)])  # 8s → 1 幕

    def _resolve(db_, model_id, mtype, scene):
        return SimpleNamespace(id="m1")

    monkeypatch.setattr(evs, "_resolve_model", _resolve)
    provider = MagicMock()
    if raise_exc:
        provider.chat.side_effect = RuntimeError("LLM 挂了")
    else:
        provider.chat.return_value = {"choices": [{"message": {"content": content}}]}
    monkeypatch.setattr(
        evs.ProviderRegistry, "for_model_id", lambda db_, mid: provider
    )
    return db, ep


def test_plan_video_script_success(monkeypatch):
    content = json.dumps({
        "scene": "雨巷夜景",
        "characters": "苏尘：素白长衫",
        "light": "冷蓝夜戏",
        "style": "Art style: 写实",
        "splits": [
            {"index": 1, "title": "主幕", "shot_indexes": [1, 2], "narrative": "苏尘撑伞缓行",
             "first_scene": "巷口全景", "last_scene": "抬眼望巷口"},
        ],
    }, ensure_ascii=False)
    db, ep = _mock_plan_env(monkeypatch, content)
    script = evs.plan_video_script(db, "ep1")
    assert script is not None
    assert ep.video_script is not None
    data = json.loads(ep.video_script)
    assert data["scene"] == "雨巷夜景"
    assert data["characters"] == "苏尘：素白长衫"
    assert data["light"] == "冷蓝夜戏"
    assert data["per_duration"] == 15
    assert len(data["splits"]) == 1
    assert data["splits"][0]["shot_indexes"] == [1, 2]
    assert data["splits"][0]["first_scene"] == "巷口全景"


def test_plan_video_script_llm_failure_returns_none(monkeypatch):
    db, ep = _mock_plan_env(monkeypatch, "", raise_exc=True)
    assert evs.plan_video_script(db, "ep1") is None
    assert ep.video_script is None


def test_plan_video_script_ignores_unknown_index(monkeypatch):
    content = json.dumps({
        "scene": "s", "characters": "c", "light": "l", "style": "st",
        "splits": [
            {"index": 1, "title": "幕1", "shot_indexes": [99], "narrative": "不存在的镜",
             "first_scene": "f", "last_scene": "l"},
            {"index": 2, "title": "幕2", "shot_indexes": [1, 2], "narrative": "镜1",
             "first_scene": "f1", "last_scene": "l1"},
        ],
    })
    db, ep = _mock_plan_env(monkeypatch, content)
    script = evs.plan_video_script(db, "ep1")
    data = json.loads(ep.video_script)
    assert len(data["splits"]) == 1 and data["splits"][0]["shot_indexes"] == [1, 2]


def test_plan_video_script_wrong_split_count_returns_none(monkeypatch):
    # LLM 输出 2 幕但目标只要 1 幕 → 规划作废
    content = json.dumps({
        "scene": "s", "characters": "c", "light": "l", "style": "st",
        "splits": [
            {"index": 1, "title": "幕1", "shot_indexes": [1], "narrative": "n1",
             "first_scene": "f1", "last_scene": "l1"},
            {"index": 2, "title": "幕2", "shot_indexes": [2], "narrative": "n2",
             "first_scene": "f2", "last_scene": "l2"},
        ],
    })
    db, ep = _mock_plan_env(monkeypatch, content)
    assert evs.plan_video_script(db, "ep1") is None
    assert ep.video_script is None


# ─── 导出收集：幕级优先 / 逐镜回退 ──────────────────────────────────


def _fake_clip(cid: str, seg_id: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(id=cid, segment_id=seg_id, video_url=url)


def test_collect_export_items_episode_video_priority(monkeypatch):
    db = MagicMock()
    ep1 = SimpleNamespace(id="ep1")
    ep2 = SimpleNamespace(id="ep2")
    ev1 = SimpleNamespace(id="ev1", index=1, video_url="http://x/ev1.mp4", status="succeeded")
    ev2 = SimpleNamespace(id="ev2", index=2, video_url="http://x/ev2.mp4", status="succeeded")
    seg_a, seg_b, seg_c = _seg(1), _seg(2), _seg(3)
    clip_x = _fake_clip("c1", "sX", "http://x/c1.mp4")

    monkeypatch.setattr(export_service, "_collect_video_clips", lambda db_, pid: [clip_x])

    # 调用顺序：episodes → (ep1) ep_videos → (ep1) segments → (ep2) ep_videos → (ep2 fallback) db.get(seg)
    db.scalars.side_effect = [
        _ScalarResult([ep1, ep2]),        # episodes
        _ScalarResult([ev1, ev2]),        # ep1 的幕级段
        _ScalarResult([seg_a, seg_b, seg_c]),  # ep1 的分镜
        _ScalarResult([]),                # ep2 无幕级视频
    ]
    db.get.return_value = _seg(5)  # ep2 的逐镜分镜（episode_id=ep1 不匹配 → 被过滤）
    items = export_service.collect_export_items(db, "p1")

    # ep1 → 2 个幕级段；ep2 逐镜分镜 episode 不匹配被过滤 → 0
    assert len(items) == 2
    assert items[0]["kind"] == "episode_video" and items[0]["episode_id"] == "ep1"
    assert items[0]["item_id"] == "ev1" and items[0]["segment_ids"] == ["s1", "s2", "s3"]
    assert items[1]["item_id"] == "ev2"


def test_collect_export_items_falls_back_to_video_clips(monkeypatch):
    db = MagicMock()
    ep1 = SimpleNamespace(id="ep1")
    clip_x = _fake_clip("c1", "sX", "http://x/c1.mp4")
    seg_x = _seg(1)
    seg_x.episode_id = "ep1"
    seg_x.id = "sX"

    monkeypatch.setattr(export_service, "_collect_video_clips", lambda db_, pid: [clip_x])
    db.scalars.side_effect = [
        _ScalarResult([ep1]),   # episodes
        _ScalarResult([]),      # ep1 无幕级视频
    ]
    db.get.return_value = seg_x
    items = export_service.collect_export_items(db, "p1")
    assert len(items) == 1
    assert items[0]["kind"] == "video_clip"
    assert items[0]["segment_id"] == "sX" and items[0]["video_url"] == "http://x/c1.mp4"


# ─── generate_designs / generate（P7.7 两阶段拆分）─────────────────

def _ep_ctx(segs, splits, per_duration=15):
    """构造 (db, ep, rows)：有效 splits + 已建行（模拟已拆幕）。"""
    db = MagicMock()
    ep = SimpleNamespace(
        id="ep1",
        project=SimpleNamespace(project_id="p1", aspect_ratio="16:9", resolution="720p"),
        video_script=json.dumps(_script(splits=splits, per_duration=per_duration), ensure_ascii=False),
        project_id="p1",
        video_status="none",
        episode_videos=[],
    )
    rows = [SimpleNamespace(id="ev1", episode_id="ep1", index=1,
                            first_frame_url=None, last_frame_url=None,
                            task_id=None, video_status="none")]
    db.get.return_value = ep
    db.scalars.return_value = _ScalarResult(segs)
    return db, ep, rows


def test_generate_designs_creates_design_task(monkeypatch):
    """阶段1：generate_designs 建行并派发 generate_episode_design 任务（只出图）。"""
    segs = [_seg(i, 4.0) for i in range(1, 4)]  # 12s ≤ 15s → 1 幕
    splits = [{"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3],
               "narrative": "幕1", "first_scene": "f", "last_scene": "l"}]
    db, ep, rows = _ep_ctx(segs, splits)

    def _fake_resolve_model(db_, model_id, mtype, scene):
        return SimpleNamespace(id="m1")
    monkeypatch.setattr(evs, "_resolve_model", _fake_resolve_model)
    monkeypatch.setattr(evs, "split_episodes", lambda db_, ep_, script, segments: [ep_])
    monkeypatch.setattr(evs, "build_episode_videos",
                        lambda db_, eps, model, w, h, per: rows)
    dispatched = {}
    monkeypatch.setattr("app.tasks.generate_episode_video.generate_episode_design_task",
                        SimpleNamespace(delay=lambda tid: dispatched.update(tid=tid)))

    result = evs.generate_designs(db, "ep1", per_duration=15)
    rows_out, task, episodes = result
    assert task.type == "generate_episode_design"
    assert rows_out == rows and episodes == [ep]
    assert dispatched["tid"] == str(task.id)


def test_generate_requires_first_frame(monkeypatch):
    """阶段2：首尾帧未生成 → generate 拒绝并抛 ValueError（强制先出图）。"""
    segs = [_seg(i, 4.0) for i in range(1, 4)]
    splits = [{"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3],
               "narrative": "幕1", "first_scene": "f", "last_scene": "l"}]
    db, ep, rows = _ep_ctx(segs, splits)
    monkeypatch.setattr(evs, "split_episodes", lambda db_, ep_, script, segments: [ep_])
    monkeypatch.setattr(evs, "list_by_episode", lambda db_, eid: rows)

    with pytest.raises(ValueError, match="首尾帧"):
        evs.generate(db, "ep1", per_duration=15)


def test_generate_passes_when_first_frame_ready(monkeypatch):
    """阶段2：首尾帧已生成 → generate 复用行并派发 generate_episode_video 任务。"""
    segs = [_seg(i, 4.0) for i in range(1, 4)]
    splits = [{"index": 1, "title": "主幕", "shot_indexes": [1, 2, 3],
               "narrative": "幕1", "first_scene": "f", "last_scene": "l"}]
    db, ep, rows = _ep_ctx(segs, splits)
    rows[0].first_frame_url = "http://x/first.png"
    rows[0].last_frame_url = "http://x/last.png"

    def _fake_resolve_model(db_, model_id, mtype, scene):
        return SimpleNamespace(id="m1")
    monkeypatch.setattr(evs, "_resolve_model", _fake_resolve_model)
    monkeypatch.setattr(evs, "split_episodes", lambda db_, ep_, script, segments: [ep_])
    monkeypatch.setattr(evs, "list_by_episode", lambda db_, eid: rows)
    dispatched = {}
    monkeypatch.setattr("app.tasks.generate_episode_video.generate_episode_video_task",
                        SimpleNamespace(delay=lambda tid: dispatched.update(tid=tid)))

    result = evs.generate(db, "ep1", per_duration=15)
    rows_out, task, episodes = result
    assert task.type == "generate_episode_video"
    assert rows_out == rows and episodes == [ep]
    assert dispatched["tid"] == str(task.id)
