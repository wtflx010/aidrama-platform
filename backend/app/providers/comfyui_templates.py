"""ComfyUI 内置工作流模板构建器（2026-08-14 从 comfyui.py 拆出）。

纯函数模块：只依赖 Python 内建类型，无任何 app 内部依赖，便于单测与维护。
占位符（__UNET__/__PROMPT__/__WIDTH__ 等）由 ComfyUIProvider._render 注入。

说明：_inject_last_frame / _apply_spectrum 仍保留在 comfyui.py。"""


def _build_img2img_omni_template(ref_count: int) -> dict:
    """Z-Image Turbo 多图生关键帧工作流（TextEncodeZImageOmni 原生多图参考，零新增模型）。

    角色/场景/道具参考图全部作为参考图注入：
    - TextEncodeZImageOmni（ComfyUI v0.30+ 内置）：image1/2/3 多图 + vae 编码注入
      reference_latents（无需 image_encoder/CLIP vision，参考图由 VAE 编码进 conditioning）
    - **latent 用主图锚定（2026-08-07 修复糊图）**：主图（refs[0]）经
      ImagePadForOutpaintTargetSize 等比外扩到目标尺寸后 VAEEncode 作 KSampler latent，
      denoise=0.85 保留主图构图/细节。此前用 EmptySD3LatentImage 纯噪声全新构图 +
      denoise=1.0，Z-Image Turbo INT8 蒸馏模型多图融合质量差 → 画面糊/失真
      （用户反馈"非常糊、画面失真"；单图 img2img 主图锚定链路从不糊）。
    - 场景/道具图不裁剪不拼接：参考图按原尺寸 VAE 编码（auto_resize 关闭），
      由模型柔和融合到画面（无 stitch_grid 拼接痕迹）。
    占位符 __IMAGE_0..N__ 由 imageToImage 注入各参考图上传后的文件名。
    """
    template: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
        # SageAttention 补丁：与 txt2img 一致
        "42": {"class_type": "PathchSageAttentionKJ", "inputs": {
            "model": ["1", 0], "sage_attention": "__SAGE_ATTN__", "allow_compile": "__SAGE_COMPILE__",
        }},
    }
    ref_inputs: dict = {}
    next_id = 4
    for i in range(min(ref_count, 3)):
        template[str(next_id)] = {
            "class_type": "LoadImage",
            "inputs": {"image": f"__IMAGE_{i}__"},
        }
        ref_inputs[f"image{i + 1}"] = [str(next_id), 0]
        next_id += 1
    omni_id = str(next_id)
    template[omni_id] = {"class_type": "TextEncodeZImageOmni", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": "__PROMPT__",
        # 关闭 auto_resize：它会把参考图 resize 到 ~1M 像素并 round 到 8 的倍数，
        # 1280×720 → 1368×768 → latent 171 为奇数，Z-Image patch_size=2 无法 patchify
        # （shape invalid 报错）。系统内资产图（四视图 1024×1024、场景封面 768p 基准、
        # 道具 1024×1024）本身是 16 的倍数，直接原尺寸 VAE 编码即可。
        "auto_resize_images": False, **ref_inputs,
    }}
    neg_id = str(next_id + 1)
    template[neg_id] = {"class_type": "CLIPTextEncode", "inputs": {
        "text": "__NEGATIVE__", "clip": ["2", 0],
    }}
    # 主图 latent 锚定链：主图（refs[0] LoadImage 节点 4）等比外扩到目标尺寸 → VAEEncode
    pad_id, latent_id = str(next_id + 2), str(next_id + 3)
    template[pad_id] = {"class_type": "ImagePadForOutpaintTargetSize", "inputs": {
        "image": ["4", 0], "target_width": "__WIDTH__", "target_height": "__HEIGHT__",
        "feathering": 8, "upscale_method": "lanczos",
    }}
    template[latent_id] = {"class_type": "VAEEncode", "inputs": {
        "pixels": [pad_id, 0], "vae": ["3", 0],
    }}
    sampler_id, dec_id, save_id = str(next_id + 4), str(next_id + 5), str(next_id + 6)
    template[sampler_id] = {"class_type": "KSampler", "inputs": {
        "seed": "__SEED__", "steps": "__STEPS__", "cfg": "__CFG__",
        "sampler_name": "euler", "scheduler": "simple", "denoise": "__DENOISE__",
        "model": ["42", 0], "positive": [omni_id, 0], "negative": [neg_id, 0],
        "latent_image": [latent_id, 0],
    }}
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    template[save_id] = {"class_type": "SaveImage", "inputs": {"images": [dec_id, 0], "filename_prefix": "ai_manju"}}
    return template


def _build_flux_ipadapter_template(ref_count: int) -> dict:
    """FLUX.1 dev (GGUF) + XLabs Flux IPAdapter 多图参考 img2img（2026-08-08 部署验证）。

    用户实测验证链路（队列工作流 4a7fb206 修正版）：
    - UnetLoaderGGUF(flux1-dev-Q5_K_S.gguf) + DualCLIPLoaderGGUF(clip_l + t5xxl gguf, type=flux)
    - VAELoader **必须用 ae.safetensors**（FLUX.1 dev latent 用 ae 解码；
      用户原配置 flux2-vae 是 FLUX.2 的 VAE → VAEDecode 崩，实测修正）
    - 多参考图 LoadImage → ImageBatch 链式拼接 → ApplyFluxIPAdapter(image) 注入
    - LoadFluxIPAdapter(ip_adapter.safetensors + clip-vit-large-patch14) + ApplyFluxIPAdapter(ip_scale)
    - XlabsSampler(steps 20, true_gs 3.5, denoise 1.0) 全新构图
    - **负向提示加 no text/no chinese text**：FLUX.1 英文原生模型中文渲染弱，
      场景参考图自带招牌会被重绘成乱码（"恩大炯茶"），负向压制后消除（实测验证）。
    占位符 __REF_IMAGE_0..N-1__ 由 imageToImage 注入各参考图上传后的文件名。
    """
    template: dict = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "__UNET__"}},
        "2": {"class_type": "DualCLIPLoaderGGUF", "inputs": {
            "clip_name1": "__CLIP1__", "clip_name2": "__CLIP2__", "type": "flux",
        }},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    }
    # 每张参考图：LoadImage → 链式 ImageBatch 拼接（image1=前一个 batch，image2=新图）
    next_id = 4
    ref_ids: list[str] = []
    for i in range(min(ref_count, 3)):
        load_id = str(next_id); next_id += 1
        template[load_id] = {"class_type": "LoadImage", "inputs": {"image": f"__REF_IMAGE_{i}__"}}
        ref_ids.append(load_id)
    batch_id = None
    if len(ref_ids) >= 2:
        for i in range(1, len(ref_ids)):
            b_id = str(next_id); next_id += 1
            # 第一张 batch 拼接 ref[0]+ref[1]，后续链式拼接上一 batch 结果 + 下一张参考图
            source = batch_id if batch_id else ref_ids[0]
            template[b_id] = {"class_type": "ImageBatch", "inputs": {
                "image1": [source, 0],
                "image2": [ref_ids[i], 0],
            }}
            batch_id = b_id
    ipa_id = str(next_id); next_id += 1
    apply_id = str(next_id); next_id += 1
    pos_id = str(next_id); next_id += 1
    neg_id = str(next_id); next_id += 1
    latent_id = str(next_id); next_id += 1
    sampler_id = str(next_id); next_id += 1
    dec_id = str(next_id); next_id += 1
    save_id = str(next_id); next_id += 1
    template[ipa_id] = {"class_type": "LoadFluxIPAdapter", "inputs": {
        "ipadatper": "__IPADAPTER__", "clip_vision": "__CLIP_VISION__", "provider": "GPU",
    }}
    template[apply_id] = {"class_type": "ApplyFluxIPAdapter", "inputs": {
        "model": ["1", 0], "ip_adapter_flux": [ipa_id, 0],
        "image": [batch_id if batch_id else ref_ids[0], 0], "ip_scale": "__IP_SCALE__",
    }}
    template[pos_id] = {"class_type": "CLIPTextEncodeFlux", "inputs": {
        "clip": ["2", 0], "clip_l": "__PROMPT__", "t5xxl": "__PROMPT__", "guidance": "__CFG__",
    }}
    # 负向：基础负面 + FLUX 中文乱码压制（场景招牌重绘乱码实测消除）
    template[neg_id] = {"class_type": "CLIPTextEncodeFlux", "inputs": {
        "clip": ["2", 0], "clip_l": "__NEGATIVE__", "t5xxl": "__NEGATIVE__", "guidance": "__CFG__",
    }}
    template[latent_id] = {"class_type": "EmptyLatentImage", "inputs": {
        "width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1,
    }}
    template[sampler_id] = {"class_type": "XlabsSampler", "inputs": {
        "model": [apply_id, 0], "conditioning": [pos_id, 0], "neg_conditioning": [neg_id, 0],
        "noise_seed": "__SEED__", "steps": "__STEPS__", "timestep_to_start_cfg": 1,
        "true_gs": "__CFG__", "image_to_image_strength": 0.0, "denoise_strength": 1.0,
        "latent_image": [latent_id, 0],
    }}
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    template[save_id] = {"class_type": "SaveImage", "inputs": {"images": [dec_id, 0], "filename_prefix": "ai_manju"}}
    return template


def _build_flux2_klein_template(ref_count: int) -> dict:
    """Flux.2 Klein 4B 多图参考编辑工作流（官方 Image Edit blueprint）。

    Flux.2 原生支持多图参考（Kontext 机制，flux_model.py forward 循环注入
    ref_latents 累积 token，无 Z-Image Turbo 蒸馏模型的多图马赛克问题）。
    **采用官方 blueprint 全新构图（2026-08-07 用户实测对齐）**：用户手动在
    ComfyUI 用官方 Image Edit blueprint + 长文提示词构图测试正常，故按官方方式：
    - EmptyFlux2LatentImage 全新构图 + Flux2Scheduler 完整 sigmas（denoise=1.0）
    - 全部参考图经 ReferenceLatent 链注入 conditioning（不主图锚定）
    **弃用主图锚定（ImagePadForOutpaintTargetSize + SplitSigmas 截断）**：1:1 四视图
    参考图被补白外扩成 16:9（角色缩小居中、左右空白）再 denoise 0.85 重绘 → 面部
    被重新生成而扭曲（用户实测"面部都是扭曲的"；官方全新构图无此问题）。
    结构：UNETLoader + PathchSageAttentionKJ(42) + CLIPLoader(type=flux2)
    + VAELoader + 参考图 scale/encode + CLIPTextEncode 正/负 + ReferenceLatent 正负链
    + Flux2Scheduler + EmptyFlux2LatentImage + RandomNoise + KSamplerSelect(euler)
    + CFGGuider + SamplerCustomAdvanced + VAEDecode + SaveImage
    占位符 __REF_IMAGE_0..N-1__ 由 imageToImage 注入各参考图上传后的文件名。
    """
    template: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
        # SageAttention 补丁：与 txt2img/img2img 一致
        "42": {"class_type": "PathchSageAttentionKJ", "inputs": {
            "model": ["1", 0], "sage_attention": "__SAGE_ATTN__", "allow_compile": "__SAGE_COMPILE__",
        }},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "flux2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    }
    # 每张参考图：LoadImage → 缩到 ~1MP → VAEEncode（官方 blueprint 用 ImageScaleToTotalPixels）
    latent_ids: list[str] = []
    next_id = 4
    for i in range(min(ref_count, 4)):
        load_id = str(next_id); next_id += 1
        scale_id = str(next_id); next_id += 1
        enc_id = str(next_id); next_id += 1
        template[load_id] = {"class_type": "LoadImage", "inputs": {"image": f"__REF_IMAGE_{i}__"}}
        template[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 1.0,
            "resolution_steps": 1,
        }}
        template[enc_id] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale_id, 0], "vae": ["3", 0]}}
        latent_ids.append(enc_id)
    pos_id, neg_id = str(next_id), str(next_id + 1)
    next_id += 2
    template[pos_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}}
    template[neg_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": "__NEGATIVE__", "clip": ["2", 0]}}
    # ReferenceLatent 链式累积：正/负 conditioning 各挂全部参考图 latent
    pos_chain, neg_chain = pos_id, neg_id
    for enc_id in latent_ids:
        p_id, n_id = str(next_id), str(next_id + 1)
        next_id += 2
        template[p_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [pos_chain, 0], "latent": [enc_id, 0],
        }}
        template[n_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [neg_chain, 0], "latent": [enc_id, 0],
        }}
        pos_chain, neg_chain = p_id, n_id
    sched_id = str(next_id); next_id += 1
    latent_id = str(next_id); next_id += 1
    noise_id = str(next_id); next_id += 1
    sampler_id = str(next_id); next_id += 1
    guider_id = str(next_id); next_id += 1
    adv_id = str(next_id); next_id += 1
    dec_id, save_id = str(next_id), str(next_id + 1)
    template[sched_id] = {"class_type": "Flux2Scheduler", "inputs": {
        "steps": "__STEPS__", "width": "__WIDTH__", "height": "__HEIGHT__",
    }}
    template[latent_id] = {"class_type": "EmptyFlux2LatentImage", "inputs": {
        "width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1,
    }}
    template[noise_id] = {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}}
    template[sampler_id] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    template[guider_id] = {"class_type": "CFGGuider", "inputs": {
        "model": ["42", 0], "positive": [pos_chain, 0], "negative": [neg_chain, 0],
        "cfg": "__CFG__",
    }}
    template[adv_id] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": [noise_id, 0], "guider": [guider_id, 0], "sampler": [sampler_id, 0],
        "sigmas": [sched_id, 0], "latent_image": [latent_id, 0],
    }}
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": [adv_id, 0], "vae": ["3", 0]}}
    template[save_id] = {"class_type": "SaveImage", "inputs": {"images": [dec_id, 0], "filename_prefix": "ai_manju"}}
    return template


def _build_flux2_klein_9b_charsheet_template(ref_count: int) -> dict:
    """Flux.2 Klein 9B (GGUF Q8_0) + CharacterSheet LoRA 四视图工作流（2026-08-09 验证）。

    封面驱动的四格合一四视图（特征格半身不裁切 + 正面/侧面/背面全身）：
    结构 = _build_flux2_klein_9b_gguf_template 的 ReferenceLatent 参考注入链 +
    中间插入 LoraLoaderModelOnly（CharacterSheet LoRA，文件名借用白名单条目
    ltxv-13b-0.9.7-distilled-lora128.safetensors，内容为 QuadView 角色设定 LoRA）：
    - UnetLoaderGGUF(flux-2-klein-9b-Q8_0.gguf) → LoraLoaderModelOnly → CFGGuider
    - 参考图（封面）LoadImage → ImageScaleToTotalPixels(1MP) → VAEEncode →
      ReferenceLatent 正/负链式注入
    - 官方蒸馏 9B：负向 ConditioningZeroOut，steps=4/cfg=1.0（capability 可覆盖）
    - 输出尺寸 __WIDTH__×__HEIGHT__（四视图 = 1536×1024，R2V 参考图规格）
    占位符 __REF_IMAGE_0..N-1__ 由 imageToImage 注入各参考图上传后的文件名。
    """
    template: dict = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "__UNET__"}},
        "1b": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": "__LORA__", "strength_model": "__LORA_STRENGTH__",
        }},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "flux2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    }
    latent_ids: list[str] = []
    next_id = 4
    for i in range(min(ref_count, 4)):
        load_id = str(next_id); next_id += 1
        scale_id = str(next_id); next_id += 1
        enc_id = str(next_id); next_id += 1
        template[load_id] = {"class_type": "LoadImage", "inputs": {"image": f"__REF_IMAGE_{i}__"}}
        template[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 1.0,
            "resolution_steps": 1,
        }}
        template[enc_id] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale_id, 0], "vae": ["3", 0]}}
        latent_ids.append(enc_id)
    pos_id, neg_id = str(next_id), str(next_id + 1)
    next_id += 2
    template[pos_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}}
    template[neg_id] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [pos_id, 0]}}
    pos_chain, neg_chain = pos_id, neg_id
    for enc_id in latent_ids:
        p_id, n_id = str(next_id), str(next_id + 1)
        next_id += 2
        template[p_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [pos_chain, 0], "latent": [enc_id, 0],
        }}
        template[n_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [neg_chain, 0], "latent": [enc_id, 0],
        }}
        pos_chain, neg_chain = p_id, n_id
    sched_id = str(next_id); next_id += 1
    latent_id = str(next_id); next_id += 1
    noise_id = str(next_id); next_id += 1
    sampler_id = str(next_id); next_id += 1
    guider_id = str(next_id); next_id += 1
    adv_id = str(next_id); next_id += 1
    dec_id, save_id = str(next_id), str(next_id + 1)
    template[sched_id] = {"class_type": "Flux2Scheduler", "inputs": {
        "steps": "__STEPS__", "width": "__WIDTH__", "height": "__HEIGHT__",
    }}
    template[latent_id] = {"class_type": "EmptyFlux2LatentImage", "inputs": {
        "width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1,
    }}
    template[noise_id] = {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}}
    template[sampler_id] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    template[guider_id] = {"class_type": "CFGGuider", "inputs": {
        "model": ["1b", 0], "positive": [pos_chain, 0], "negative": [neg_chain, 0],
        "cfg": "__CFG__",
    }}
    template[adv_id] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": [noise_id, 0], "guider": [guider_id, 0], "sampler": [sampler_id, 0],
        "sigmas": [sched_id, 0], "latent_image": [latent_id, 0],
    }}
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": [adv_id, 0], "vae": ["3", 0]}}
    template[save_id] = {"class_type": "SaveImage", "inputs": {"images": [dec_id, 0], "filename_prefix": "ai_manju"}}
    return template


def _build_flux2_klein_9b_gguf_template(ref_count: int) -> dict:
    """Flux.2 Klein 9B (GGUF Q8_0) 多图参考编辑工作流（2026-08-08 实测验证）。

    背景：fp8 版 9B 在 comfy-kitchen 0.2.26 + torch 2.12 反量化 block 量化 FLUX
    模型数值错误 → 彩色噪点（对照实验确认）；GGUF Q8_0 走 UnetLoaderGGUF 反量化
    路径正常。本模板与 _k9b_gguf_2ref.py 实测通过链路一致：
    - UnetLoaderGGUF(flux-2-klein-9b-Q8_0.gguf) + CLIPLoader(qwen_3_8b, type=flux2)
      + VAELoader(full_encoder_small_decoder)
    - 参考图 LoadImage → ImageScaleToTotalPixels(1MP) → VAEEncode
    - 正向 CLIPTextEncode(prompt) + 负向 ConditioningZeroOut（官方蒸馏 9B 负向归零）
    - ReferenceLatent 链式累积（正/负各挂全部参考图 latent）
    - Flux2Scheduler(steps 4) + EmptyFlux2LatentImage + RandomNoise
      + KSamplerSelect(euler) + CFGGuider(cfg 1.0) + SamplerCustomAdvanced
      + VAEDecode + SaveImage
    官方蒸馏参数：steps=4、cfg=1.0（capability 可覆盖）。
    占位符 __REF_IMAGE_0..N-1__ 由 imageToImage 注入各参考图上传后的文件名。
    """
    template: dict = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "__UNET__"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "flux2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    }
    # 每张参考图：LoadImage → 缩到 ~1MP → VAEEncode（与 4B 模板一致）
    latent_ids: list[str] = []
    next_id = 4
    for i in range(min(ref_count, 4)):
        load_id = str(next_id); next_id += 1
        scale_id = str(next_id); next_id += 1
        enc_id = str(next_id); next_id += 1
        template[load_id] = {"class_type": "LoadImage", "inputs": {"image": f"__REF_IMAGE_{i}__"}}
        template[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 1.0,
            "resolution_steps": 1,
        }}
        template[enc_id] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale_id, 0], "vae": ["3", 0]}}
        latent_ids.append(enc_id)
    pos_id, neg_id = str(next_id), str(next_id + 1)
    next_id += 2
    template[pos_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}}
    # 官方蒸馏 9B 负向：ConditioningZeroOut（cfg=1.0 蒸馏模型负向归零）
    template[neg_id] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [pos_id, 0]}}
    # ReferenceLatent 链式累积：正/负 conditioning 各挂全部参考图 latent
    pos_chain, neg_chain = pos_id, neg_id
    for enc_id in latent_ids:
        p_id, n_id = str(next_id), str(next_id + 1)
        next_id += 2
        template[p_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [pos_chain, 0], "latent": [enc_id, 0],
        }}
        template[n_id] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [neg_chain, 0], "latent": [enc_id, 0],
        }}
        pos_chain, neg_chain = p_id, n_id
    sched_id = str(next_id); next_id += 1
    latent_id = str(next_id); next_id += 1
    noise_id = str(next_id); next_id += 1
    sampler_id = str(next_id); next_id += 1
    guider_id = str(next_id); next_id += 1
    adv_id = str(next_id); next_id += 1
    dec_id, save_id = str(next_id), str(next_id + 1)
    template[sched_id] = {"class_type": "Flux2Scheduler", "inputs": {
        "steps": "__STEPS__", "width": "__WIDTH__", "height": "__HEIGHT__",
    }}
    template[latent_id] = {"class_type": "EmptyFlux2LatentImage", "inputs": {
        "width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1,
    }}
    template[noise_id] = {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}}
    template[sampler_id] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    template[guider_id] = {"class_type": "CFGGuider", "inputs": {
        "model": ["1", 0], "positive": [pos_chain, 0], "negative": [neg_chain, 0],
        "cfg": "__CFG__",
    }}
    template[adv_id] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": [noise_id, 0], "guider": [guider_id, 0], "sampler": [sampler_id, 0],
        "sigmas": [sched_id, 0], "latent_image": [latent_id, 0],
    }}
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": [adv_id, 0], "vae": ["3", 0]}}
    template[save_id] = {"class_type": "SaveImage", "inputs": {"images": [dec_id, 0], "filename_prefix": "ai_manju"}}
    return template

# Wan2.2 TI2V 图生视频：UNET + CLIP(wan) + VAE + WanImageToVideo → KSampler → CreateVideo → SaveVideo
_IMG2VID_MINIMAX_TEMPLATE = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
    # 注意力：生产默认原生（stock）。sol-attn 变体见 _IMG2VID_MINIMAX_TEMPLATE_SOL（草稿/预览用）
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "minimax"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE_AUDIO__"}},
    "5": {"class_type": "LoadImage", "inputs": {"image": "__IMAGE__"}},
    # P8：Turbo 蒸馏 LoRA。2026-08-27：默认 lightx2v v1.1 768p bf16（comfyui 版），
    # 用标准 LoraLoaderModelOnly 加载（model-only，不需要 CLIP）。勿用
    # LoraLoaderInt8ConvRot——其源码强制文件含 comfy_quant int8 矩阵，bf16 文件直接报错；
    # 只有 int8convrot 格式（v0.1 comfy_int8convrot 系列）才走 LoraLoaderInt8ConvRot。
    # 未配置 lora_name 时由调用方移除该节点
    "41": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["1", 0],
        "lora_name": "__LORA__", "strength_model": 1.0,
    }},
    "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": "__PROMPT__",
        "width": "__WIDTH__", "height": "__HEIGHT__", "length": "__FRAMES__",
        "first_frame": ["5", 0],
    }},
    "7": {"class_type": "MiniMaxH3SigmaShift", "inputs": {
        "model": ["41", 0], "shift_video": "__SHIFT_VIDEO__", "shift_audio": "__SHIFT_AUDIO__",
    }},
    "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}},
    "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    "10": {"class_type": "BasicScheduler", "inputs": {
        "model": ["7", 0], "scheduler": "simple", "steps": "__STEPS__", "denoise": 1.0,
    }},
    "11": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["6", 0]}},
    "12": {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0],
        "sigmas": ["10", 0], "latent_image": ["6", 1],
    }},
    "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
    "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
    "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": "__FPS__", "audio": ["14", 0]}},
    "16": {"class_type": "SaveVideo", "inputs": {
        "video": ["15", 0], "filename_prefix": "video/minimax", "format": "mp4", "codec": "h264",
    }},
}


# sol-attn 加速变体（草稿/预览；生产用 stock 保证面部/口型质量，见 H3资源库 08 笔记）
_IMG2VID_MINIMAX_TEMPLATE_SOL = {
    **_IMG2VID_MINIMAX_TEMPLATE,
    "42": {"class_type": "MiniMaxH3MemoryEfficientSolAttentionPatch", "inputs": {
        "model": ["1", 0], "enabled": True, "tau": 1.3, "min_tokens": 4096,
        "strict": False, "thresh_type": "diag", "int8_qk": False, "int8_pv": False,
        "sink_conditioning": "exact_kv_and_rows", "dense_blocks": "",
    }},
    "41": {**_IMG2VID_MINIMAX_TEMPLATE["41"], "inputs": {**_IMG2VID_MINIMAX_TEMPLATE["41"]["inputs"], "model": ["42", 0]}},
}


# ── MiniMax H3 融合单文件 SLA 模板（T2V/I2V）──────────────────────
# MATLOWAI minimax-h3-fused-refdelta-r1024-turbo8-mystic07 融合模型：pruned fl2va +
# refdelta(ref2va−fl2va 差值 rank-1024 SVD) 熔合 + turbo8(lightx2v 8 步) + Mystic 动作平滑
# LoRA 烘焙 + 整体 INT8 ConvRot。单文件一个模型通吃 T2V/I2V/Ref2V，turbo/mystic 已烘焙进
# 权重（无需单独 LoRA，lora_name 应留空）。
# 配套注意力：H3SLAAttention（PlagueKind 节点，block-sparse Triton 内核，是 lightx2v SLA turbo
# LoRA 训练时匹配的推理路径）。官方示例 workflow/04_i2v_fl2v_4step_sla.api.json：
#   UNETLoader → H3SLAAttention(sparsity_ratio=0.9, block_size=64, min_seq_len=8192,
#                               dense_last_steps=0, protect_audio=true, enabled=true)
#   → MiniMaxH3SigmaShift(shift 12/3) → [ImageToVideo 条件] → BasicScheduler(simple,4 步)
#   + BasicGuider → SamplerCustomAdvanced(sampler=res_multistep) → VAEDecode(+Audio) → CreateVideo
# 采样链与 sol/stock 变体不同：sampler 用官方 res_multistep（非 euler），step 上限 ~4-8。
_IMG2VID_MINIMAX_FUSION_TEMPLATE = {
    # MiniMaxChunkFeedForward：官方 lowvram 参考链（UNET→Chunk→H3SLA），16GB 卡降峰值显存。
    "902": {"class_type": "MiniMaxChunkFeedForward", "inputs": {"model": ["1", 0], "chunks": 4, "seq_threshold": 4096}},
    "900": {"class_type": "H3SLAAttention", "inputs": {
        "model": ["902", 0], "sparsity_ratio": 0.9, "block_size": "64",
        "min_seq_len": 8192, "dense_last_steps": 0, "protect_audio": True, "enabled": True,
    }},
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "minimax"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE_AUDIO__"}},
    "5": {"class_type": "LoadImage", "inputs": {"image": "__IMAGE__"}},
    "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": "__PROMPT__",
        "width": "__WIDTH__", "height": "__HEIGHT__", "length": "__FRAMES__",
        "first_frame": ["5", 0],
    }},
    "7": {"class_type": "MiniMaxH3SigmaShift", "inputs": {
        "model": ["900", 0], "shift_video": "__SHIFT_VIDEO__", "shift_audio": "__SHIFT_AUDIO__",
    }},
    "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}},
    "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "10": {"class_type": "BasicScheduler", "inputs": {
        "model": ["7", 0], "scheduler": "simple", "steps": "__STEPS__", "denoise": 1.0,
    }},
    "11": {"class_type": "BasicGuider", "inputs": {"model": ["7", 0], "conditioning": ["6", 0]}},
    "12": {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0],
        "sigmas": ["10", 0], "latent_image": ["6", 1],
    }},
    "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
    "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
    "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": "__FPS__", "audio": ["14", 0]}},
    "16": {"class_type": "SaveVideo", "inputs": {
        "video": ["15", 0], "filename_prefix": "video/minimax_fusion", "format": "mp4", "codec": "h264",
    }},
}


def _build_minimax_ref_template(
    ref_count: int, ref_image_size: str = "match", ref_video_count: int = 0, use_sol: bool = False,
    use_sla: bool = False
) -> dict:
    """构建 MiniMax H3 R2V（Reference to Video）工作流模板。

    多模态参考（对标 Seedance 多锚点做法）：ref_images 每张图一个 LoadImage 节点，
    按 ref_image_0..N 接入 MiniMaxH3ReferenceToVideo（节点原生支持 ≤9 张参考图）；
    ref_videos 每个视频一个 LoadVideo + GetVideoComponents 节点，frames 接
    ref_videos.ref_video_0..N、paired 音轨接 ref_video_audios.ref_video_audio_0..N
    （节点原生支持 ≤3 个参考视频，每个可带自己的配乐）。
    R2V 使用独立 ref2va unet 权重（capability.unet_name 指定；ref_image_size 仅为参考图缩放参数），
    capability.video_kind="minimax_ref" 启用。
    占位符 __REF_IMAGE_0..N__ / __REF_VIDEO_0..N__ 由调用方注入各参考上传后的文件名。

    2026-08-09：整体对齐用户 ComfyUI 手工验证工作流
    「mnimax h3 ref2va 4步 多图参考工作流」（服务端 history 5213d35b / 78fdb873 均 success）：
      - 采样链（2026-08-27 起）：UNET → LoraLoaderModelOnly(strength 0.75) → SigmaShift
        → MiniMaxH3MemoryEfficientSageAttentionPatch → MiniMaxH3SigmaShift → KSampler
      - KSampler 直出：sampler_name=er_sde / scheduler=simple / cfg=1.0 / denoise=1.0（4 步），
        不用 SamplerCustomAdvanced 链（用户工作流实测该直链可跑通）
      - CreateVideo bit_depth=8；SaveVideo format/codec=auto
    """
    template: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
        # Turbo 蒸馏 LoRA。2026-08-27：默认 lightx2v bf16 comfyui 版（图生视频用 v1.1 768p，
        # R2V 用 ref2v v0.1 comfyui bf16），标准 LoraLoaderModelOnly 加载；勿用
        # LoraLoaderInt8ConvRot（bf16 文件无 comfy_quant int8 矩阵会报错）。
        # strength 0.75 为用户工作流实测值。
        "41": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0],
            "lora_name": "__LORA__", "strength_model": 0.75,
        }},
        # 注意力：生产默认原生（stock）；capability.sol_attention=True 时下面按 use_sol 注入 sol-attn（草稿/预览）
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "minimax"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE_AUDIO__"}},
    }
    if use_sla:
        # 融合单文件模型官方 SLA 路径：UNET(1) → MiniMaxChunkFeedForward(902,lowvram 降显存)
        # → H3SLAAttention(900) → SigmaShift。block_size 必须传字符串 "64"
        #（object_info 为 COMBO 枚举 ["64","128"]，传 int 会校验失败；对齐 166 冒烟实测），
        # sparsity_ratio=0.9 对应官方示例。
        template["902"] = {"class_type": "MiniMaxChunkFeedForward", "inputs": {
            "model": ["1", 0], "chunks": 4, "seq_threshold": 4096,
        }}
        template["900"] = {"class_type": "H3SLAAttention", "inputs": {
            "model": ["902", 0], "sparsity_ratio": 0.9, "block_size": "64",
            "min_seq_len": 8192, "dense_last_steps": 0, "protect_audio": True, "enabled": True,
        }}
    if use_sol:
        template["42"] = {"class_type": "MiniMaxH3MemoryEfficientSolAttentionPatch", "inputs": {
            "model": ["41", 0], "enabled": True, "tau": 1.3, "min_tokens": 4096,
            "strict": False, "thresh_type": "diag", "int8_qk": False, "int8_pv": False,
            "sink_conditioning": "exact_kv_and_rows", "dense_blocks": "",
        }}
    if use_sla:
        attn_ref: list = ["900", 0]
    elif use_sol:
        attn_ref = ["42", 0]
    else:
        attn_ref = ["41", 0]
    next_id = 5
    cond_inputs: dict = {
        "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
        "prompt": "__PROMPT__", "width": "__WIDTH__", "height": "__HEIGHT__",
        "length": "__FRAMES__", "ref_image_size": ref_image_size,
    }
    for i in range(min(ref_count, 9)):
        template[str(next_id)] = {
            "class_type": "LoadImage",
            "inputs": {"image": f"__REF_IMAGE_{i}__"},
        }
        # ComfyUI V3 API（Autogrow）：ref_images 动态输入必须用扁平点分 key
        # （ref_images.ref_image_N）。嵌套 dict 会被展开为空 {} → 参考图从未进入模型
        # （2026-08-08 实测根因：旧格式 refs=0，改扁平 key 后 refs=5 正常注入）
        cond_inputs[f"ref_images.ref_image_{i}"] = [str(next_id), 0]
        next_id += 1
    # 参考视频：LoadVideo（file=__REF_VIDEO_i__，ComfyUI /upload/video 上传）→
    # GetVideoComponents 拆出 frames(IMAGE) + audio(AUDIO)，分别接 ref_videos / ref_video_audios。
    # GetVideoComponents 输出顺序：[images, audio, fps, bit_depth]（nodes_video.py 核心节点）
    for i in range(min(ref_video_count, 3)):
        load_id, comp_id = str(next_id), str(next_id + 1)
        template[load_id] = {"class_type": "LoadVideo", "inputs": {"file": f"__REF_VIDEO_{i}__"}}
        template[comp_id] = {"class_type": "GetVideoComponents", "inputs": {"video": [load_id, 0]}}
        cond_inputs[f"ref_videos.ref_video_{i}"] = [comp_id, 0]
        cond_inputs[f"ref_video_audios.ref_video_audio_{i}"] = [comp_id, 1]
        next_id += 2
    cond_id = str(next_id)
    template[cond_id] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": cond_inputs,
    }
    sigma_id, sampler_id = str(next_id + 1), str(next_id + 2)
    dec_id, dec_audio_id = str(next_id + 3), str(next_id + 4)
    video_id, save_id = str(next_id + 5), str(next_id + 6)
    template[sigma_id] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {
        "model": attn_ref, "shift_video": "__SHIFT_VIDEO__", "shift_audio": "__SHIFT_AUDIO__",
    }}
    if use_sla:
        # 融合单文件 SLA 官方采样链：KSamplerSelect(res_multistep) + BasicScheduler(simple)
        # + BasicGuider + SamplerCustomAdvanced（对齐 workflow/05_ref2va_4step_sla.api.json）。
        ksel_id, sched_id, guider_id, adv_id = str(next_id + 7), str(next_id + 8), str(next_id + 9), str(next_id + 10)
        template[ksel_id] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}}
        template[sched_id] = {"class_type": "BasicScheduler", "inputs": {
            "model": [sigma_id, 0], "scheduler": "simple", "steps": "__STEPS__", "denoise": 1.0,
        }}
        template[guider_id] = {"class_type": "BasicGuider", "inputs": {"model": [sigma_id, 0], "conditioning": [cond_id, 0]}}
        template[adv_id] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": [sampler_id, 0], "guider": [guider_id, 0], "sampler": [ksel_id, 0],
            "sigmas": [sched_id, 0], "latent_image": [cond_id, 1],
        }}
        # RandomNoise 用 __SEED__（模板自带）；sampler_id 复用为 RandomNoise 节点
        template[sampler_id] = {"class_type": "RandomNoise", "inputs": {"noise_seed": "__SEED__"}}
        sample_out: list = [adv_id, 0]
    else:
        template[sampler_id] = {"class_type": "KSampler", "inputs": {
            "model": [sigma_id, 0], "positive": [cond_id, 0], "negative": [cond_id, 0],
            "latent_image": [cond_id, 1], "seed": "__SEED__", "steps": "__STEPS__",
            "cfg": "__CFG__", "sampler_name": "er_sde", "scheduler": "simple", "denoise": 1.0,
        }}
        sample_out = [sampler_id, 0]
    template[dec_id] = {"class_type": "VAEDecode", "inputs": {"samples": sample_out, "vae": ["3", 0]}}
    template[dec_audio_id] = {"class_type": "VAEDecodeAudio", "inputs": {
        "samples": sample_out, "vae": ["4", 0],
    }}
    template[video_id] = {"class_type": "CreateVideo", "inputs": {
        "images": [dec_id, 0], "fps": "__FPS__", "audio": [dec_audio_id, 0], "bit_depth": 8,
    }}
    template[save_id] = {"class_type": "SaveVideo", "inputs": {
        "video": [video_id, 0], "filename_prefix": "video/minimax_ref", "format": "auto", "codec": "auto",
    }}
    return template


def _build_upscale_template(
    tier: str = "4x",
    target_width: int = 1920,
    target_height: int = 1080,
    src_fps: float = 24.0,
    prefix: str = "upscale",
) -> dict:
    """视频超分工作流（逐帧画质超分 → 归一化目标分辨率 → mp4）。

    节点链路（均在服务器实测存在）：VHS_LoadVideo(上传原片 mp4) →
    ImageUpscaleWithModel(4x-UltraSharp / RealESRGAN_x2plus 逐帧 SR) →
    [各档追加 ImageBlur 轻量抑晕] → ImageScale(lanczos 归一到目标宽高) →
    CreateVideo(显式源帧率) → SaveVideo。音频不随超分输出，由后端下载后
    ffmpeg 混回原音轨并做帧数/时长校验，保证与原片逐帧对齐（杜绝帧率/
    时长漂移与末帧异常）。

    - tier="4x"：4x-UltraSharp.pth（动漫/线条特化强锐，边缘光晕副作用明显）→ 用强抑晕（2/1.8）
    - tier="2x"：RealESRGAN_x2plus.pth（更克制、快 5 倍）→ 用温和抑晕（1/1.2）
    - 2026-08-23 起两档均追加抑晕（此前仅 4x 档有，2x 缺抑晕 → 边缘重影/亮边）；
      2026-08-31 起抑晕强度按档区分——4x UltraSharp 对真人脸过锐/高光副作用大，加压制
    """
    is_4x = str(tier).lower() == "4x"
    model_name = "4x-UltraSharp.pth" if is_4x else "RealESRGAN_x2plus.pth"
    # 2026-08-31：抑晕强度按档区分——4x-UltraSharp 是动漫/线条特化强锐模型，
    # 真人脸超分会放大边缘光晕/过锐高光，用更强的抑晕压掉；2x RealESRGAN 更克制，
    # 用温和抑晕即可。此前两档均 sigma=1.0，对 4x 档过度锐化的压制不足。
    if is_4x:
        blur_radius, sigma = 2, 1.8
    else:
        blur_radius, sigma = 1, 1.2
    t: dict = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": "__VIDEO__", "force_rate": 0, "custom_width": 0, "custom_height": 0,
            "frame_load_cap": 0, "skip_first_frames": 0, "select_every_nth": 1,
        }},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model_name}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": ["2", 0], "image": ["1", 0],
        }},
    }
    # 抑晕：轻量高斯模糊后再缩放，削弱 SR 模型边缘光晕/过度锐化。
    # 2026-08-23：2x 档也启用（此前仅 4x 档有，2x 缺抑晕 → 边缘重影/亮边）
    t["4"] = {"class_type": "ImageBlur", "inputs": {
        "image": ["3", 0], "blur_radius": blur_radius, "sigma": sigma,
    }}
    last = "4"
    t["5"] = {"class_type": "ImageScale", "inputs": {
        "image": [last, 0], "upscale_method": "lanczos",
        "width": int(target_width), "height": int(target_height), "crop": "disabled",
    }}
    t["6"] = {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "fps": float(src_fps)}}
    t["7"] = {"class_type": "SaveVideo", "inputs": {
        "video": ["6", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto",
    }}
    return t


def _build_ltx_refine_template(
    prompt: str,
    negative: str,
    steps: int = 8,
    denoise: float = 0.22,
    seed: int = 42,
    ic_lora_strength: float = 1.0,
    prefix: str = "refine",
) -> dict:
    """LTX-2.5「原生分辨率精修（只精修不放大）」工作流（2026-08-27 实测定案 E2A）。

    给已有视频做 LTX-2.5 native 重渲：VHS_LoadVideo → VAEEncode →
    LoraLoaderModelOnly(ltx-2.3-22b-ic-lora-ingredients-0.9, strength=ic_lora_strength) →
    KSampler(euler/simple cfg=1.0 steps=8 denoise=0.22——实测 denoise>0.4 会把脸洗软) → VAEDecode →
    VHS_VideoCombine(带回原音轨)。**不放大**（无 LatentUpscaler），无 guide（中生视频
    叠首帧参考会重影，见踩坑库 41）。

    A/B 实测（同一条 480p/5s 片，脸区 Laplacian）：原片 533 → 8步/0.22/IC1.0 = 524
    （全场最高但≈原片）；6步/0.3/IC0.9=507、6步/0.25/IC1.0=513、8步/0.45=238（洗软）。
    结论：精修=柔化兜底不劣化，别指望它超越源分辨率。
    """
    t: dict = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": "__VIDEO__", "force_rate": 0, "custom_width": 0, "custom_height": 0,
            "frame_load_cap": 0, "skip_first_frames": 0, "select_every_nth": 1,
        }},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-video-vae-conv-bf16.safetensors"}},
        "3": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "LTX-2.5-Distilled-Q4_K_M.gguf"}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "type": "ltxv"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": "__PROMPT__"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": "__NEGATIVE__"}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["3", 0], "lora_name": "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
            "strength_model": ic_lora_strength,
        }},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["2", 0]}},
        "14": {"class_type": "KSampler", "inputs": {
            "model": ["7", 0], "positive": ["5", 0], "negative": ["6", 0],
            "latent_image": ["9", 0], "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
        }},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["2", 0]}},
        "16": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["15", 0], "audio": ["1", 2], "frame_rate": 24, "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True,
        }},
    }
    return t

def _build_minimax_h3_director_template(
    task_type: str = "r2v — 参考主体生视频(Reference to Video)",
) -> dict:
    """MiniMax H3 Director 多段连续生视频工作流模板（2026-08-29）。

    导演台节点内部完成：分段计划 → MiniMaxH3ImageToVideo/ReferenceToVideo 条件编码
    → MiniMaxH3SigmaShift → KSampler → AV 解码（images + audio）。模板只负责组装
    模型/双 VAE/CLIP 四输入链路，并把整片（images[0]）+ 原生立体声（audio[1]）
    交给 SaveVideo 落盘。timeline_data 为导演台时间轴 JSON 字符串。
    段间引导/二采均为节点内部行为，不在模板展开。
    """
    template: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
        "13": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": "__LORA__", "strength_model": 0.75,
        }},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "minimax"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE_AUDIO__"}},
        "5": {"class_type": "MiniMaxH3Director", "inputs": {
            "model": ["13", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0], "clip": ["2", 0],
            "task_type": task_type,
            "global_prompt": "__GLOBAL_PROMPT__",
            "frame_rate": "__FPS__", "width": "__WIDTH__", "height": "__HEIGHT__",
            "ref_max_size": "__REF_MAX__", "total_frames": "__TOTAL_FRAMES__",
            "bd_grp_sample": "采样设置",  # BDGROUP 分组控件为必填（ComfyUI API 校验,2026-08-30 实测）
            "timeline_data": "__TIMELINE__",
            "bd_grp_advanced": "高级采样",
            "steps": "__STEPS__", "sampler": "__SAMPLER__", "scheduler": "__SCHEDULER__",
            "cfg": "__CFG__", "seed": "__SEED__", "control_after_generate": "randomize",
            "bd_grp_perf": "性能",
            "shift_video": "__SHIFT_VIDEO__", "shift_audio": "__SHIFT_AUDIO__",
            "clear_vram_between_segments": True, "export_source_images": False,
        }},
        "6": {"class_type": "CreateVideo", "inputs": {
            "images": ["5", 0], "fps": "__FPS__", "audio": ["5", 1], "bit_depth": 8,
        }},
        "7": {"class_type": "SaveVideo", "inputs": {
            "video": ["6", 0], "filename_prefix": "video/director",
            "format": "auto", "codec": "auto",
        }},
    }
    return template

