"""批量「初始帧来源」分镜 gen_params 写入测试（prev_tail / none 相互清除）。

2026-09 修复「上一分镜尾帧不生效」：
- 选 prev_tail 写 reference_src=prev_tail 并清 custom_first_frame_url；
- 选 none 必须重置 reference_src=none，否则上一批遗留的 prev_tail/custom
  误触发链式串行/首帧覆盖，后续批次「初始帧来源」看似不生效。
"""
from types import SimpleNamespace

from app.services.batch_service import _apply_batch_ref_source


def _seg(gen_params=None):
    return SimpleNamespace(gen_params=dict(gen_params or {}))


def test_prev_tail_sets_flag_and_clears_custom():
    segs = [_seg({"custom_first_frame_url": "http://x/c.png"})]
    assert _apply_batch_ref_source(segs, "prev_tail", None) is True
    assert segs[0].gen_params == {"reference_src": "prev_tail"}


def test_none_clears_stale_prev_tail_and_custom():
    segs = [_seg({"reference_src": "prev_tail", "custom_first_frame_url": "http://x/c.png"})]
    assert _apply_batch_ref_source(segs, "none", None) is False
    assert segs[0].gen_params == {"reference_src": "none"}


def test_custom_sets_url_and_does_not_trigger_chain():
    segs = [_seg(None)]
    assert _apply_batch_ref_source(segs, "custom", "http://x/c.png") is False
    assert segs[0].gen_params == {"reference_src": "custom", "custom_first_frame_url": "http://x/c.png"}


def test_prev_tail_any_segments_returns_true():
    segs = [_seg(None), _seg({"reference_src": "none"})]
    assert _apply_batch_ref_source(segs, "prev_tail", None) is True
    assert all(s.gen_params.get("reference_src") == "prev_tail" for s in segs)


def test_unknown_ref_src_leaves_segments_untouched():
    segs = [_seg({"reference_src": "prev_tail"})]
    _apply_batch_ref_source(segs, "", None)
    assert segs[0].gen_params == {"reference_src": "prev_tail"}
