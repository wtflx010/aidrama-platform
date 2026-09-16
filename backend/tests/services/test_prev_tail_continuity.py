"""prev_tail 提示词承接块测试（2026-09 修复首帧与提示词不匹配致画面错乱）。

上一镜尾帧被锁为硬首帧，但本镜提示词可能描述与首帧不一致的开端情绪/景别，
模型硬拼两种内容 → 面部扭曲/突变。承接块声明「以首帧为准、平滑过渡」压制冲突。
"""
from app.tasks.generate_video import build_prev_tail_continuity


def test_continuity_mentions_prev_branch():
    block = build_prev_tail_continuity("女主错愕震惊，嘴唇微张")
    assert "首帧已锁定" in block
    assert "以首帧为准" in block
    assert "平滑地过渡" in block
    assert "女主错愕震惊" in block


def test_continuity_no_prev_desc_skips_reference():
    block = build_prev_tail_continuity(None)
    assert "上一镜结束状态参考" not in block


def test_continuity_truncates_long_desc():
    block = build_prev_tail_continuity("甲" * 300)
    assert len(block) < 2000


def test_continuity_prepends_to_prompt():
    prompt = "镜头缓慢推近至女友面部特写"
    out = build_prev_tail_continuity("上一镜惊愕张口") + "\n" + prompt
    assert out.endswith(prompt)
    assert out.startswith("【承接上一镜")
