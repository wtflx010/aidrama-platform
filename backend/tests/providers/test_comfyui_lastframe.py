"""P6 首尾帧：ComfyUI MiniMax H3 尾帧注入测试（wan2.2 模板已随 LTX/Wan 下线移除）。"""
from app.providers.comfyui import (
    _IMG2VID_MINIMAX_TEMPLATE,
    _build_img2img_omni_template,
    _build_minimax_ref_template,
    _inject_last_frame,
)


def test_minimax_template_has_first_frame():
    node = _IMG2VID_MINIMAX_TEMPLATE["6"]
    assert node["class_type"] == "MiniMaxH3ImageToVideo"
    assert node["inputs"]["first_frame"] == ["5", 0]


def test_inject_last_frame_adds_end_image_node():
    t = _inject_last_frame(_IMG2VID_MINIMAX_TEMPLATE, "end.png")
    new_id = str(max(int(k) for k in _IMG2VID_MINIMAX_TEMPLATE) + 1)
    assert t[new_id]["class_type"] == "LoadImage"
    assert t[new_id]["inputs"]["image"] == "end.png"
    assert t["6"]["inputs"]["last_frame"] == [new_id, 0]


def test_inject_last_frame_does_not_mutate_original():
    _inject_last_frame(_IMG2VID_MINIMAX_TEMPLATE, "end.png")
    assert "last_frame" not in _IMG2VID_MINIMAX_TEMPLATE["6"]["inputs"]


# ─── P6 Phase2：MiniMax H3 R2V 模板 ──────────────────────────────


def test_build_minimax_ref_template_structure():
    t = _build_minimax_ref_template(3, "match")
    refs = [n for n in t.values() if n["class_type"] == "LoadImage"]
    assert len(refs) == 3
    cond = next(n for n in t.values() if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    # ComfyUI V3 API（Autogrow）：ref_images 动态输入用扁平点分 key（ref_images.ref_image_N），
    # 嵌套 dict 会被展开为空 {} 导致参考图丢失（2026-08-08 实测根因）
    assert cond["inputs"]["ref_images.ref_image_0"][0] in t
    assert cond["inputs"]["ref_images.ref_image_2"][0] in t
    assert "ref_images" not in cond["inputs"]
    assert cond["inputs"]["ref_image_size"] == "match"
    assert "audio_vae" in cond["inputs"]
    # 采样链闭合：所有节点引用均存在（无悬空 id）
    for node in t.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0].isdigit():
                assert v[0] in t, f"悬空引用节点 {v[0]}"


def test_build_minimax_ref_template_caps_at_9():
    t = _build_minimax_ref_template(12, "max")
    refs = [n for n in t.values() if n["class_type"] == "LoadImage"]
    assert len(refs) == 9
    cond = next(n for n in t.values() if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    assert "ref_images.ref_image_8" in cond["inputs"]
    assert "ref_images.ref_image_9" not in cond["inputs"]


# ─── 多图生关键帧：TextEncodeZImageOmni 模板 ──────────────────────


def test_build_img2img_omni_template_multi_ref():
    """多图参考：3 张参考图全部接入 TextEncodeZImageOmni（image1/2/3）+ 主图 latent 锚定。"""
    t = _build_img2img_omni_template(3)
    refs = [n for n in t.values() if n["class_type"] == "LoadImage"]
    assert len(refs) == 3
    omni = next(n for n in t.values() if n["class_type"] == "TextEncodeZImageOmni")
    assert omni["inputs"]["image1"] == ["4", 0]
    assert omni["inputs"]["image2"] == ["5", 0]
    assert omni["inputs"]["image3"] == ["6", 0]
    # VAE 编码参考 latent（注入 reference_latents），无需 CLIP vision image_encoder
    assert omni["inputs"]["vae"] == ["3", 0]
    assert "image_encoder" not in omni["inputs"]
    # 主图 latent 锚定：主图(节点4) 等比外扩 → VAEEncode → KSampler latent（非空 latent 全新构图）
    pad = next(n for n in t.values() if n["class_type"] == "ImagePadForOutpaintTargetSize")
    assert pad["inputs"]["image"] == ["4", 0]
    assert pad["inputs"]["target_width"] == "__WIDTH__"
    sampler = next(n for n in t.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["latent_image"][0] == next(
        k for k, n in t.items() if n["class_type"] == "VAEEncode"
    )
    # denoise 由调用方注入（0.85 保留主图细节，修复糊图）
    assert sampler["inputs"]["denoise"] == "__DENOISE__"
    # 采样链闭合：无悬空节点引用
    for node in t.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0].isdigit():
                assert v[0] in t, f"悬空引用节点 {v[0]}"


def test_build_img2img_omni_template_caps_at_3():
    """Omni 上限 3 张参考图（image1/2/3），超限截断。"""
    t = _build_img2img_omni_template(5)
    refs = [n for n in t.values() if n["class_type"] == "LoadImage"]
    assert len(refs) == 3
    omni = next(n for n in t.values() if n["class_type"] == "TextEncodeZImageOmni")
    assert set(omni["inputs"]) >= {"image1", "image2", "image3"}


def test_build_img2img_omni_template_single_ref():
    """单图回退：仅 image1 接入（走原 img2img 模板单图锚定逻辑由 imageToImage 分支决定）。"""
    t = _build_img2img_omni_template(1)
    refs = [n for n in t.values() if n["class_type"] == "LoadImage"]
    assert len(refs) == 1
    omni = next(n for n in t.values() if n["class_type"] == "TextEncodeZImageOmni")
    assert omni["inputs"]["image1"] == ["4", 0]
    assert "image2" not in omni["inputs"]
    assert "image3" not in omni["inputs"]