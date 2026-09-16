"""_prompt_with_speech：一条台词必须在一个镜头内完整说完（不截断、不留半句）。

2026-09-09 用户反馈音频问题：说话飘/吐词不清/无台词也开口。
根因：旧 _cap 按时长超容硬切台词到句边界，语义断裂致 H3 语速飘、自补词。
修复：保留完整台词；单条超出镜头容量时改引导「语速稍快、一口气说完」+ 加「只念引号内台词」防幻觉。
"""
from types import SimpleNamespace
from app.services.project_longvideo_service import _prompt_with_speech


def _seg(dur=8.0, dialogue=None, narration=None, face=True):
    return SimpleNamespace(
        description="特写，人物正对镜头" if face else "全景，人物背影",
        duration=dur,
        dialogue_lines=dialogue or [],
        narration=narration,
    )


def test_short_dialogue_kept_complete_without_hint():
    # 25 字 < 容量(8s*5.5≈41)：完整保留，不截断、不加提速提示
    seg = _seg(dur=8.0, dialogue=[{"speaker": "林浅", "text": "慢慢才想明白……最伤人的，从来不是大吵大闹的分开。"}])
    p = _prompt_with_speech(seg, "全景镜头")
    assert "慢慢才想明白" in p
    assert "从来不是大吵大闹的分开" in p      # 句尾完整，未被掐
    assert "【口播要求】" not in p            # 未超容量，不加提速
    assert "之外不开口说任何话" in p          # 防幻觉说明始终带


def test_overlong_dialogue_kept_whole_with_pacing_hint():
    # 53 字 > 容量(8s*5.5≈41)：保完整、不截断，附加提速说完提示
    seg = _seg(dur=8.0, dialogue=[{"speaker": "林浅", "text": "是我一直掏心掏肺，处处迁就事事包容。明明什么都没做错，可到最后……却输得满心委屈，连一句挽留都说不出口"}])
    p = _prompt_with_speech(seg, "全景镜头")
    assert "明明什么都没做错，可到最后……却输得满心委屈" in p   # 整句完整保留（含标点未截断）
    assert "【口播要求】" in p                              # 超容量 → 提速说完
    assert "语速稍快、干脆利落，一字不落" in p


def test_narration_kept_complete():
    # 旁白同样保完整、不截断
    seg = _seg(dur=8.0, narration="后来才发现，所有的坚持和迁就，好像只感动了我自己。")
    p = _prompt_with_speech(seg, "中景跟拍")
    assert "好像只感动了我自己" in p
    assert "【口播要求】" not in p


def test_backshot_dialogue_as_voiceover_not_on_screen():
    # 远景/背影 → 对白作为画外音旁白，不硬让背对镜头开口
    seg = _seg(dur=8.0, dialogue=[{"speaker": "林浅", "text": "以前真的挺傻的。"}], face=False)
    p = _prompt_with_speech(seg, "全景，人物背影")
    assert "旁白" in p and "画外音" in p
    assert "正对镜头开口" not in p

