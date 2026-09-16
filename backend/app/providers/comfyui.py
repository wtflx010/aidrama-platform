"""ComfyUI 适配器：文生图 / 图生图 / 图生视频（异步工作流 + 轮询 history）。

流程：
1. 图生图/图生视频先把参考图上传到 ComfyUI（POST /upload/image）
2. 渲染工作流模板（API 格式 JSON，含占位符）→ POST /prompt 提交
3. 轮询 GET /history/{prompt_id} 直到成功/失败
4. 提取输出文件（images/gifs/videos）→ 组装可下载 /view URL

工作流模板来源：
- capability.workflow：用户自配（从 ComfyUI 导出的 API 格式，含占位符）
- 未配置 → 内置默认模板（FLUX 文生图/图生图 + Wan2.2 图生视频）

内置模板默认参数（capability 可覆盖）：
- steps/cfg/denoise：采样参数
- ckpt_name：checkpoint 文件名（未配置时自动检测第一个）
- clip_name/unet_name/vae_name：Wan 视频模型文件名
"""
import logging
import random
import time
import uuid
from urllib.parse import quote

import httpx

from app.providers.base import (
    BaseProvider,
    ImageOpts,
    ProviderStatus,
    TaskHandle,
    TaskResult,
    VideoOpts,
)
from app.providers.errors import ProviderError, map_to_chinese
from app.utils.media import clean_remote_url
from app.providers.comfyui_templates import (
    _build_flux2_klein_9b_charsheet_template,
    _build_flux2_klein_9b_gguf_template,
    _build_flux2_klein_template,
    _build_flux_ipadapter_template,
    _build_img2img_omni_template,
    _build_ltx_refine_template,
    _build_minimax_h3_director_template,
    _build_minimax_ref_template,
    _IMG2VID_MINIMAX_FUSION_TEMPLATE,
    _build_upscale_template,
    _IMG2VID_MINIMAX_TEMPLATE,
    _IMG2VID_MINIMAX_TEMPLATE_SOL,
)  # noqa: E501, F401  re-export：模板构建器与常量供 tests / 内部引用

logger = logging.getLogger(__name__)


# ─── 内置工作流模板（API 格式）──────────────────────────────────────

# Z-Image Turbo 文生图：UNETLoader(z_image_turbo) + CLIPLoader(qwen_3_4b, type=qwen_image) + VAELoader(ae)
# + PathchSageAttentionKJ（SageAttention 加速，Ada Lovelace FP8 路径）
# Turbo 蒸馏模型（z_image_turbo_int8_convrot）：
# - steps 官方默认 8，cfg=1.0（蒸馏模型 CFG 强制 1），euler/simple
# - SageAttention: KJNodes PathchSageAttentionKJ 节点，sageattn_qk_int8_pv_fp8_cuda 最优
# - allow_compile=true: 对 sageattn 函数启用 torch.compile（二次加速，首帧编译开销可接受）
_TXT2IMG_TEMPLATE = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "qwen_image"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    # SageAttention 补丁：KJNodes 通用节点，QK INT8 + PV FP8 CUDA（Ada Lovelace 最优）
    "42": {"class_type": "PathchSageAttentionKJ", "inputs": {
        "model": ["1", 0], "sage_attention": "__SAGE_ATTN__", "allow_compile": "__SAGE_COMPILE__",
    }},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}},
    "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "__NEGATIVE__", "clip": ["2", 0]}},
    "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1}},
    # 可选 LoRA（2026-09 东方审美接入）：capability/opts 配置 LORA 时插入；未配置时
    # textToImage 移除本节点并把下游 model 重连回 ["42", 0]，不影响现有生成。
    "1c": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["42", 0], "lora_name": "__LORA__", "strength_model": "__LORA_STRENGTH__",
    }},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": "__SEED__", "steps": "__STEPS__", "cfg": "__CFG__",
        "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        "model": ["1c", 0], "positive": ["4", 0], "negative": ["9", 0],
        "latent_image": ["5", 0],
    }},
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
    "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "ai_manju"}},
}

# Z-Image base（非蒸馏）文生图：fp8_e4m3fn 加载 + CLIPLoader type=lumina2
# + ModelSamplingAuraFlow(shift=3) + res_multistep/simple + steps 30 / cfg 4.0。
# 2026-08-08 实测结论（对照实验 + 官方模板确认）：
# - bf16 全精度 12.3GB 超出 16GB 显存 → UNet 权重加载被截断 → 全黑图；
#   weight_dtype=fp8_e4m3fn 加载（省一半显存）后正常出图。
# - S3-DiT 是 flow-matching 架构，必须 ModelSamplingAuraFlow(shift=3)，
#   缺它 sigma 分布错误 → 全黑输出。
# - CLIPLoader type=lumina2（官方 image_z_image.json 模板；qwen_image 也能跑）。
# - 不挂 PathchSageAttentionKJ：Sage INT8 补丁与 bf16 base 全精度不兼容 → 红马赛克
#   （对照：去 Sage 后红马赛克消失）。
_TXT2IMG_ZIMAGE_BASE_TEMPLATE = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "__WEIGHT_DTYPE__"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "lumina2"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    # S3-DiT flow-matching shift 采样节点（官方 base 模板 shift=3）
    "5": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}},
    "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "__NEGATIVE__", "clip": ["2", 0]}},
    "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1}},
    # 可选 LoRA（2026-09 东方审美接入），未配置时 textToImage 移除并重连回 ["5", 0]。
    "1c": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["5", 0], "lora_name": "__LORA__", "strength_model": "__LORA_STRENGTH__",
    }},
    "7": {"class_type": "KSampler", "inputs": {
        "seed": "__SEED__", "steps": "__STEPS__", "cfg": "__CFG__",
        "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0,
        "model": ["1c", 0], "positive": ["4", 0], "negative": ["9", 0],
        "latent_image": ["6", 0],
    }},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "ai_manju"}},
}

# Z-Image Turbo 图生图：LoadImage → 等比缩放+外扩至目标尺寸 + VAEEncode 后 KSampler（denoise 控制保留度）
# 尺寸修复：img2img 若直接 VAEEncode 参考图，输出尺寸=参考图原尺寸（角色四视图 1:1 会破坏项目比例）。
# 2026-08-07 修复：ImageScale(crop=center) 会把 1:1 角色图裁剪成项目比例（如 16:9），
# 角色被裁掉头/脚后重绘补全 → 角色变异/扭曲（用户反馈"角色不一致+个别扭曲"）。
# 改用 ImagePadForOutpaintTargetSize：等比缩放到目标尺寸内 + 四周外扩填充（不裁剪），
# 外扩区域由模型按 prompt 自然重绘为背景，角色主体完整保留。
_IMG2IMG_TEMPLATE = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "__UNET__", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "__CLIP__", "type": "qwen_image"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "__VAE__"}},
    # SageAttention 补丁：与 txt2img 一致
    "42": {"class_type": "PathchSageAttentionKJ", "inputs": {
        "model": ["1", 0], "sage_attention": "__SAGE_ATTN__", "allow_compile": "__SAGE_COMPILE__",
    }},
    "4": {"class_type": "LoadImage", "inputs": {"image": "__IMAGE__"}},
    # 等比缩放 + 外扩至 __WIDTH__x__HEIGHT__（不裁剪参考图，外扩区由模型重绘为背景）
    "41": {"class_type": "ImagePadForOutpaintTargetSize", "inputs": {
        "image": ["4", 0], "target_width": "__WIDTH__", "target_height": "__HEIGHT__",
        "feathering": 8, "upscale_method": "lanczos",
    }},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}},
    "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "__NEGATIVE__", "clip": ["2", 0]}},
    "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["41", 0], "vae": ["3", 0]}},
    "7": {"class_type": "KSampler", "inputs": {
        "seed": "__SEED__", "steps": "__STEPS__", "cfg": "__CFG__",
        "sampler_name": "euler", "scheduler": "simple", "denoise": "__DENOISE__",
        "model": ["42", 0], "positive": ["5", 0], "negative": ["9", 0],
        "latent_image": ["6", 0],
    }},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "ai_manju"}},
}

def _inject_last_frame(template: dict, end_image_name: str) -> dict:
    """给 MiniMax H3 模板注入尾帧：新增 LoadImage 节点并接到 MiniMaxH3ImageToVideo.last_frame。

    P6 首尾帧衔接：仅当有尾帧（下一镜关键帧）时调用；无尾帧用原模板（避免空 LoadImage）。
    """
    import copy

    t = copy.deepcopy(template)
    max_id = max(int(k) for k in t.keys())
    end_id = str(max_id + 1)
    t[end_id] = {"class_type": "LoadImage", "inputs": {"image": end_image_name}}
    for node in t.values():
        if node.get("class_type") == "MiniMaxH3ImageToVideo":
            node["inputs"]["last_frame"] = [end_id, 0]
    return t


def _apply_spectrum(template: dict) -> dict:
    """在采样链插入 SpectrumApplyMiniMaxH3（谱特征预测跳过 transformer 步骤，~30% 加速）。

    社区推荐位置：MiniMax H3 模型/LoRA → SigmaShift → Spectrum → BasicScheduler/BasicGuider。
    只跳过部分 transformer 求值，当前步的输出头/重建/sigma 映射仍完整执行；
    Euler/RES multistep 支持，不支持的采样器自动回退原生路径。
    找不到 SigmaShift 节点时原样返回（回退原生采样，不报错）。
    """
    import copy

    t = copy.deepcopy(template)
    sigma_id = next(
        (k for k, v in t.items() if v.get("class_type") == "MiniMaxH3SigmaShift"), None
    )
    if sigma_id is None:
        return template
    max_id = max(int(k) for k in t.keys())
    spec_id = str(max_id + 1)
    t[spec_id] = {"class_type": "SpectrumApplyMiniMaxH3", "inputs": {
        "model": [sigma_id, 0],
        "enabled": True,
        "blend_weight": 0.5,
        "degree": 1,
        "ridge_lambda": 0.1,
        "window_size": 2.0,
        "flex_window": 0.75,
        "warmup_steps": 1,
        "tail_actual_steps": 1,
        "max_history": 8,
        "debug": False,
        "history_storage": "system_ram",
        "bootstrap_first_forecast": True,
    }}
    for node in t.values():
        if node.get("class_type") in ("BasicScheduler", "BasicGuider"):
            if node["inputs"].get("model") == [sigma_id, 0]:
                node["inputs"]["model"] = [spec_id, 0]
    return t

# MiniMax H3 模型默认文件名（capability 可覆盖）
# FL2VA 扩散主干 + Qwen3-VL 文本编码器 + 视频/音频双 VAE（AV 联合 latent 解码）
# 2026-09-09：接入融合单文件模型 minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot
#（pruned fl2va + refdelta(ref2va−fl2va rank-1024 SVD) + turbo8(lightx2v) + Mystic 动作平滑 LoRA
#  熔合 + 整体 INT8 ConvRot；单文件通吃 T2V/I2V/Ref2V）。turbo/mystic 已烘焙 → lora_name 留空，
#  provider 自动移除 LoRA 节点；模型链 UNET→MiniMaxChunkFeedForward→H3SLAAttention→SigmaShift。

def _default_minimax_models() -> dict:
    return {
        "unet_name": "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors",
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae_name": "minimax_h3_video_vae_fp16.safetensors",
        "vae_audio_name": "minimax_h3_audio_vae_fp32.safetensors",
        "lora_name": "",
    }


# MiniMax H3 输出分辨率档位（capability.minimax_res 指定，默认 0.7mp 官方主推档）。
# 2026-09-09 接入融合单文件模型（minimax_h3_fused_refdelta_r1024_turbo8）后，默认档切官方主推
# 0.7MP（1152×640）；新增官方 16GB 低显存基准 0.5MP（960×544）。保留 480p/720p/768p 作为
# 历史/兼容档位（1080p 仅作超分产物，非生成档）。
# H3 latent 16 倍空间下采样 + patch_size=2 → 宽高必须是 32 的倍数。
# 注意：720p(1280×720) 与 1080p(1920×1080) 非 32 倍数，不能直接作 H3 生成档
#（720p 项目会由 provider 回退到最近生成档；1080p 仅超分，见 video_service）。
_MMAX_RES_GRADES = {
    "0.1mp": {
        "16:9": (416, 224), "9:16": (224, 416),
        "4:3": (352, 256), "3:4": (256, 352), "1:1": (320, 320),
    },
    "0.2mp": {
        "16:9": (576, 320), "9:16": (320, 576),
        "4:3": (480, 384), "3:4": (384, 480), "1:1": (416, 416),
    },
    "0.25mp": {
        "16:9": (640, 352), "9:16": (352, 640),
        "4:3": (544, 416), "3:4": (416, 544), "1:1": (480, 480),
    },
    "0.3mp": {
        "16:9": (704, 384), "9:16": (384, 704),
        "4:3": (608, 448), "3:4": (448, 608), "1:1": (512, 512),
    },
    "0.4mp": {
        "16:9": (832, 480), "9:16": (480, 832),
        "4:3": (736, 544), "3:4": (544, 736), "1:1": (640, 640),
    },
    "0.6mp": {
        "16:9": (1088, 608), "9:16": (608, 1088),
        "4:3": (928, 704), "3:4": (704, 928), "1:1": (800, 800),
    },
    "0.8mp": {
        "16:9": (1216, 672), "9:16": (672, 1216),
        "4:3": (1056, 768), "3:4": (768, 1056), "1:1": (896, 896),
    },
    "0.9mp": {
        "16:9": (1280, 704), "9:16": (704, 1280),
        "4:3": (1088, 832), "3:4": (832, 1088), "1:1": (960, 960),
    },
    "1.0mp": {
        "16:9": (1344, 768), "9:16": (768, 1344),
        "4:3": (1184, 864), "3:4": (864, 1184), "1:1": (1024, 1024),
    },
    "0.5mp": {  # 官方 16GB 低显存基准（8 步 turbo 原生训练尺寸）
        "16:9": (960, 544),
        "9:16": (544, 960),
        "4:3": (704, 528),
        "3:4": (528, 704),
        "1:1": (512, 512),
    },
    "0.7mp": {  # 官方主推 recipe（0.7MP，16:9）
        "16:9": (1152, 640),
        "9:16": (640, 1152),
        "4:3": (768, 576),
        "3:4": (576, 768),
        "1:1": (672, 672),
    },
    "480p": {
        "16:9": (832, 480),
        "9:16": (480, 832),
        "4:3": (640, 480),
        "3:4": (480, 640),
        "1:1": (480, 480),
    },
    "768p": {
        "16:9": (1344, 768),
        "9:16": (768, 1344),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
        "1:1": (768, 768),
    },
    "720p": {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "4:3": (960, 720),
        "3:4": (720, 960),
        "1:1": (720, 720),
    },
}

# Z-Image Turbo 模型默认文件名（capability 可覆盖）
# 蒸馏文生图/图生图：diffusion 主干 + Qwen3-4B 文本编码器（CLIPLoader type=qwen_image）+ Flux ae VAE
# int8_convrot 量化版：比 bf16 更快、显存更省（RTX 4070 Ti SUPER 16GB 实测可用）
_DEFAULT_ZIMAGE_MODELS = {
    "unet_name": "z_image_turbo_int8_convrot.safetensors",
    "clip_name": "qwen_3_4b_fp8_mixed.safetensors",
    "vae_name": "ae.safetensors",
}

# Flux.2 Klein 4B 模型默认文件名（capability 可覆盖）
# 多图参考编辑（Image Edit blueprint）：diffusion 主干 flux-2-klein-base-4b-fp8
# + Qwen3-4B 文本编码器（CLIPLoader type=flux2，注意非 T5）+ flux2-vae
# 原生支持多图参考（Kontext 机制，forward 循环注入 ref_latents，无 Z-Image 马赛克问题）
_DEFAULT_FLUX2_MODELS = {
    "unet_name": "flux-2-klein-base-4b-fp8.safetensors",
    "clip_name": "qwen_3_4b_fp8_mixed.safetensors",
    "vae_name": "flux2-vae.safetensors",
}

# FLUX.1 dev (GGUF) + XLabs Flux IPAdapter 模型默认文件名（capability 可覆盖）
# 2026-08-08 部署验证：UnetLoaderGGUF + DualCLIPLoaderGGUF(clip_l + t5xxl) + ae VAE
# + LoadFluxIPAdapter(ip_adapter.safetensors + clip-vit-large-patch14) + ApplyFluxIPAdapter
# + XlabsSampler。多图参考（ImageBatch 拼接 ≤3 张，官方 2-3 张最优）。
_DEFAULT_FLUX_IPADAPTER_MODELS = {
    "unet_name": "flux1-dev-Q5_K_S.gguf",
    "clip1_name": "clip_l.safetensors",
    "clip2_name": "t5-v1_1-xxl-encoder-Q8_0.gguf",
    "vae_name": "ae.safetensors",
    "ipadapter_name": "ip_adapter.safetensors",
    "clip_vision_name": "clip-vit-large-patch14.safetensors",
}

# Flux.2 Klein 9B (GGUF Q8_0) 模型默认文件名（capability 可覆盖）
# 2026-08-08 实测验证：fp8 版 9B 在 comfy-kitchen 0.2.26 + torch 2.12 反量化
# block 量化 FLUX 模型数值错误 → 彩色噪点（对照实验确认）；GGUF Q8_0 走
# UnetLoaderGGUF 反量化路径正常（纯文生图 + 双参考均验证通过）。
# 文本编码器 Qwen3-8B（CLIPLoader type=flux2）+ full_encoder_small_decoder VAE
#（官方子图定义：distilled 9B 用 full_encoder_small_decoder）。
_DEFAULT_FLUX2_9B_GGUF_MODELS = {
    "unet_name": "flux-2-klein-9b-Q8_0.gguf",
    "clip_name": "qwen_3_8b_fp8mixed.safetensors",
    "vae_name": "full_encoder_small_decoder.safetensors",
}

# SageAttention 加速配置（KJNodes 节点）：
# - sageattn_qk_int8_pv_fp8_cuda: QK INT8 + PV FP8 CUDA，Ada Lovelace (RTX 40/50) 最优
# - auto: 自动选择最佳后端
# - disabled: 禁用（回退原生 attention）
# allow_compile: 对 sageattn 函数本身启用 torch.compile（需 sageattn 2.2.0+）
# 2026-08-25：CUDA13 升级后 SageAttention（2.2.0+cu128 轮子）与 cu130 不兼容（_fused DLL 加载失败），
# 默认改为 disabled（回退原生注意力）；H3 已改用 sol-attn；Sage 官方 cu130 轮子可用后再恢复。
_DEFAULT_SAGE_ATTN = "disabled"
_DEFAULT_SAGE_COMPILE = False

# 输出类型：image / video
_OUTPUT_KEYS = ("images", "gifs", "videos")


def _parse_capability(cap: dict) -> dict:
    return cap or {}


class ComfyUIProvider(BaseProvider):
    provider_type = "comfyui"

    def __init__(self, model_config, res_override: str | None = None):
        """res_override：项目级视频分辨率（"480p"/"720p"/"768p"）——覆盖 capability 的
        minimax_res 档位，使项目内全部视频按统一分辨率生成（2026-08-16）。
        """
        super().__init__(model_config)
        self._res_override = res_override

    # ─── 基础 ─────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        return self.cfg.endpoint.rstrip("/")

    def _in_queue(self, prompt_id: str) -> bool:
        """ComfyUI /queue 是否仍含该 prompt_id（运行中或排队中）。

        /queue 返回 {"queue_running": [[number, prompt_id, spec], ...],
        "queue_pending": [...]}。history 查无记录时用它区分「任务真在跑」
        和「记录已被清理（远程早已结束）」——后者是卡死回收的关键信号。
        网络异常保守视为在队列（避免误回收还在跑的任务）。
        """
        try:
            q = self.http.get(f"{self._endpoint()}/queue", timeout=10).json()
            for key in ("queue_running", "queue_pending"):
                for item in q.get(key) or []:
                    pid = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None
                    if pid == prompt_id:
                        return True
            return False
        except Exception:
            return True

    def _cap(self) -> dict:
        cap = _parse_capability(self.cfg.capability)
        # 项目级分辨率覆盖：ComfyUI 视频模型（MiniMax H3 / LTX-2.5）按项目分辨率档位生成
        if self._res_override:
            vk = str(cap.get("video_kind", ""))
            if vk.startswith("minimax"):
                _ov = str(self._res_override).lower()
                # 2026-09-09 融合模型官方档：H3 latent 需 32 倍数，720p/1080p 非 32 倍数
                # 不能直接生成 → 回退到模型官方主推档（0.7mp），避免 patchify 报错。
                if _ov in ("720p", "1080p"):
                    _ov = "0.7mp"
                cap["minimax_res"] = _ov
        return cap

    def _cap_num(self, key: str, default):
        try:
            return type(default)(self._cap().get(key, default))
        except (TypeError, ValueError):
            return default

    # ─── 工作流模板 ───────────────────────────────────────────────

    def _template(self, kind: str) -> dict:
        """取工作流模板：capability.workflow[kind] 优先，否则内置默认。

        2026-08-19 配方变体：capability.workflow_variant 非空时优先取
        workflow[f"{kind}_{variant}"]（如 img2vid_gradest）——用于采样配方
        A/B（官方梯度估计/双阶段 vs 旧 EulerAncestral）切换而不改默认模板。
        """
        cap = self._cap()
        wf = cap.get("workflow")
        if isinstance(wf, dict):
            variant = str(cap.get("workflow_variant", "") or "")
            if variant and wf.get(f"{kind}_{variant}"):
                return wf[f"{kind}_{variant}"]
            if wf.get(kind):
                return wf[kind]
        if kind == "img2img":
            return _IMG2IMG_TEMPLATE
        if kind == "img2vid":
            # P8：图生视频默认 MiniMax H3（wan2.2/LTX 已下线）；capability.video_kind 可显式指定
            # 注意力：生产默认 stock（quality-first，sol 稀疏实测面部锐度 -2.2x，见 08/34 笔记）；
            # capability.sol_attention=True 时切 sol-attn 加速变体（草稿/预览用）
            if str(cap.get("sol_attention", "")).lower() in ("1", "true", "yes"):
                return _IMG2VID_MINIMAX_TEMPLATE_SOL
            # 融合单文件模型官方 SLA 路径（capability.sla=True）：H3SLAAttention(res_multistep)，
            # 无独立 LoRA（turbo/mystic 已烘焙进权重），见 comfyui_templates 融合模板 注释。
            if str(cap.get("sla", "")).lower() in ("1", "true", "yes"):
                return _IMG2VID_MINIMAX_FUSION_TEMPLATE
            return _IMG2VID_MINIMAX_TEMPLATE
        # 文生图：Z-Image base（capability.zimg_base=True）用 base 专用模板
        #（fp8 加载 + lumina2 + ModelSamplingAuraFlow + res_multistep）；
        # 否则 Z-Image Turbo 模板（蒸馏 cfg=1.0 + SageAttention 加速）。
        if kind == "txt2img" and cap.get("zimg_base"):
            return _TXT2IMG_ZIMAGE_BASE_TEMPLATE
        return _TXT2IMG_TEMPLATE

    def _first_option(self, node: str, param: str) -> str | None:
        """从 /object_info/{node} 取参数第一个可选值（如 checkpoint 名）。"""
        try:
            r = self.http.get(f"{self._endpoint()}/object_info/{node}", timeout=15)
            r.raise_for_status()
            info = r.json().get(node, {})
            req = info.get("input", {}).get("required", {})
            opts = req.get(param, [None, None])
            if isinstance(opts, list) and opts and isinstance(opts[0], list) and opts[0]:
                return opts[0][0]
        except Exception as e:
            logger.warning("解析 %s.%s 可选值失败: %s", node, param, e)
        return None

    def _node_available(self, node_type: str) -> bool:
        """探测 ComfyUI 是否安装了指定节点（object_info）。"""
        try:
            r = self.http.get(f"{self._endpoint()}/object_info/{node_type}", timeout=15)
            return r.status_code == 200 and node_type in r.json()
        except Exception as e:
            logger.warning("探测节点 %s 失败: %s", node_type, e)
            return False

    def _resolve_zimage_models(self) -> dict:
        """解析 Z-Image Turbo 模型文件名：capability 覆盖默认值。"""
        cap = self._cap()
        return {
            k: cap.get(k) or default
            for k, default in _DEFAULT_ZIMAGE_MODELS.items()
        }

    def _resolve_flux2_models(self) -> dict:
        """解析 Flux.2 Klein 4B 模型文件名：capability 覆盖默认值。"""
        cap = self._cap()
        return {
            k: cap.get(k) or default
            for k, default in _DEFAULT_FLUX2_MODELS.items()
        }

    def _resolve_flux2_9b_gguf_models(self) -> dict:
        """解析 Flux.2 Klein 9B (GGUF Q8_0) 模型文件名：capability 覆盖默认值。"""
        cap = self._cap()
        return {
            k: cap.get(k) or default
            for k, default in _DEFAULT_FLUX2_9B_GGUF_MODELS.items()
        }

    def _resolve_flux_ipadapter_models(self) -> dict:
        """解析 FLUX.1 dev + XLabs IPAdapter 模型文件名：capability 覆盖默认值。"""
        cap = self._cap()
        return {
            k: cap.get(k) or default
            for k, default in _DEFAULT_FLUX_IPADAPTER_MODELS.items()
        }

    def _resolve_sage_attn(self) -> tuple[str, bool]:
        """解析 SageAttention 配置：capability.sage_attention / sage_compile 覆盖默认值。

        返回 (sage_attention_mode, allow_compile)：
        - sage_attention: sageattn_qk_int8_pv_fp8_cuda（默认，Ada 最优）/ auto / disabled
        - allow_compile: True（默认，对 sageattn 函数启用 torch.compile）
        """
        cap = self._cap()
        sage_attn = cap.get("sage_attention", _DEFAULT_SAGE_ATTN)
        if sage_attn is False or str(sage_attn).lower() == "false":
            sage_attn = "disabled"
        sage_compile = cap.get("sage_compile", _DEFAULT_SAGE_COMPILE)
        if sage_compile is False or str(sage_compile).lower() == "false":
            sage_compile = False
        else:
            sage_compile = True
        return str(sage_attn), bool(sage_compile)

    def _resolve_ckpt(self) -> str:
        cap = self._cap()
        if cap.get("ckpt_name"):
            return cap["ckpt_name"]
        ckpt = self._first_option("CheckpointLoaderSimple", "ckpt_name")
        if ckpt:
            return ckpt
        raise ProviderError("无法确定 checkpoint：请在工作流模板或 capability.ckpt_name 指定")

    def _render(self, template: dict, variables: dict) -> dict:
        """递归替换占位符：仅含 __XXX__ 的字符串被替换并尝试转 int/float。

        节点连接数组（如 ["1", 1]）中的字符串节点 ID 不含占位符，原样保留。
        """

        def _coerce(s: str):
            if s.lower() in ("true", "false"):
                return s.lower() == "true"
            try:
                return int(s)
            except ValueError:
                pass
            try:
                return float(s)
            except ValueError:
                pass
            return s

        def _walk(v):
            if isinstance(v, dict):
                return {k: _walk(val) for k, val in v.items()}
            if isinstance(v, list):
                return [_walk(i) for i in v]
            if isinstance(v, str):
                if "__" in v:
                    for key, val in variables.items():
                        v = v.replace(f"__{key}__", str(val))
                    return _coerce(v)
                return v
            return v

        return _walk(template)

    # ─── 图片上传 ─────────────────────────────────────────────────

    def _upload_image(self, image_url: str) -> dict:
        """把本地 media URL 图片上传到 ComfyUI /upload/image，返回 {name, subfolder, type}。

        仅支持本地 /static/media/ URL（系统内图片均落盘本地）；公网 URL 报错。
        2026-08-07 修复：上传文件名用「相对路径转义」保证唯一——此前用 basename，
        角色/场景/道具封面都叫 cover.png，overwrite=true 互相覆盖，多图参考时
        所有 LoadImage 指向同一张图（角色丢失，画面只剩场景）。
        """
        marker = "/static/media/"
        if marker not in image_url:
            raise ProviderError(
                f"ComfyUI 参考图必须是本地 media URL（当前: {image_url[:80]}）"
            )
        from app.config import settings
        import os
        local_path = os.path.join(settings.media_dir, image_url.split(marker, 1)[1])
        if not os.path.exists(local_path):
            raise ProviderError(f"参考图文件不存在: {local_path}")
        # 唯一化文件名：assets/xxx/cover.png → assets__xxx__cover.png（保留目录层级防同名覆盖）
        rel = image_url.split(marker, 1)[1]
        safe_name = rel.replace("/", "__").replace("\\", "__")
        with open(local_path, "rb") as f:
            files = {"image": (safe_name, f, "image/png")}
            data = {"overwrite": "true", "type": "input"}
            r = self.http.post(
                f"{self._endpoint()}/upload/image", files=files, data=data, timeout=60
            )
        r.raise_for_status()
        return r.json()  # {name, subfolder, type}

    def _upload_video(self, video_url: str) -> dict:
        """把本地 media URL 视频上传到 ComfyUI input 目录，返回 {name, subfolder, type}。

        供 R2V 参考视频（LoadVideo 节点 file 参数）使用。仅支持本地 /static/media/ URL。
        2026-08-11 实测：ComfyUI 0.31.0 无 /upload/video 路由（POST 405）；/upload/image
        不校验内容类型，视频文件按原样落盘 input 目录，LoadVideo 的文件列表即可识别
        （video_upload 标记只影响前端上传入口）。端点与字段名沿用 /upload/image + image。
        """
        marker = "/static/media/"
        if marker not in video_url:
            raise ProviderError(
                f"ComfyUI 参考视频必须是本地 media URL（当前: {video_url[:80]}）"
            )
        from app.config import settings
        import os
        local_path = os.path.join(settings.media_dir, video_url.split(marker, 1)[1])
        if not os.path.exists(local_path):
            raise ProviderError(f"参考视频文件不存在: {local_path}")
        rel = video_url.split(marker, 1)[1]
        safe_name = rel.replace("/", "__").replace("\\", "__")
        with open(local_path, "rb") as f:
            files = {"image": (safe_name, f, "video/mp4")}
            data = {"overwrite": "true", "type": "input"}
            r = self.http.post(
                f"{self._endpoint()}/upload/image", files=files, data=data, timeout=180
            )
        r.raise_for_status()
        return r.json()  # {name, subfolder, type}

    # ─── 提交 + 轮询 ──────────────────────────────────────────────

    def _submit(self, workflow: dict) -> tuple[str, str]:
        """提交工作流，返回 (prompt_id, client_id)。

        2026-08-10：client_id 由每次提交唯一生成，供 WebSocket（/ws?clientId=）
        订阅该任务的实时 progress 事件（ComfyUI 的 progress 消息只发给匹配
        client_id 的连接），从而实现真实进度上报而非时间估算。
        """
        client_id = uuid.uuid4().hex
        body = {"prompt": workflow, "client_id": client_id}
        r = self.http.post(f"{self._endpoint()}/prompt", json=body, timeout=60)
        r.raise_for_status()
        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            raise ProviderError("ComfyUI 提交工作流未返回 prompt_id")
        return prompt_id, client_id

    def cancel(self, provider_task_id: str) -> None:
        """取消 ComfyUI 上的生成任务（best-effort，不抛错）。

        先查询 /queue 判断目标任务所处阶段，精准取消，避免误伤其他排队任务：
        - 目标在 queue_pending（排队未执行）→ POST /queue {"delete": [id]} 从队列删除；
        - 目标在 queue_running（正在执行）→ POST /interrupt 中断当前执行；
        - 目标不在队列（已结束/被删除）→ 无需操作。
        ComfyUI 的 /interrupt 是全局中断（中断当前执行的 prompt），只有在目标任务
        确实正在运行时才调用，否则会误打断其他任务。
        """
        ep = self._endpoint()
        try:
            r = self.http.get(f"{ep}/queue", timeout=15)
            r.raise_for_status()
            data = r.json()
            running = {item[1] for item in data.get("queue_running", [])}
            pending = {item[1] for item in data.get("queue_pending", [])}
        except Exception as e:
            logger.warning("ComfyUI 查询队列失败 %s: %s", provider_task_id, e)
            return
        if provider_task_id in pending:
            try:
                r = self.http.post(f"{ep}/queue", json={"delete": [provider_task_id]}, timeout=15)
                r.raise_for_status()
                logger.info("ComfyUI 已从队列删除任务 %s", provider_task_id)
            except Exception as e:
                logger.warning("ComfyUI 删除队列任务失败 %s: %s", provider_task_id, e)
        elif provider_task_id in running:
            try:
                r = self.http.post(f"{ep}/interrupt", timeout=15)
                r.raise_for_status()
                logger.info("ComfyUI 已中断执行任务 %s", provider_task_id)
            except Exception as e:
                logger.warning("ComfyUI 中断任务失败 %s: %s", provider_task_id, e)
        else:
            logger.info("ComfyUI 任务 %s 不在队列中，无需取消", provider_task_id)

    # ─── 三个能力入口 ─────────────────────────────────────────────

    def textToImage(self, prompt: str, opts: ImageOpts) -> TaskHandle:
        cap = self._cap()
        # Flux.2 Klein 4B 文生图（capability.ref_engine=flux2）：
        # 复用 Klein 编辑模板（0 参考图 = 纯文生图），文本编码器 type=flux2（Qwen3-4B）
        if cap.get("ref_engine") == "flux2":
            return self._text_to_image_flux2(prompt, opts)
        # Flux.2 Klein 9B (GGUF) 文生图（capability.ref_engine=flux2_9b_gguf）：
        # 官方蒸馏参数 steps=4 / cfg=1.0，负向 ConditioningZeroOut（蒸馏模型负向归零）
        if cap.get("ref_engine") == "flux2_9b_gguf":
            return self._text_to_image_flux2_9b_gguf(prompt, opts)
        width, height = self._size(opts)
        zimg = self._resolve_zimage_models()
        sage_attn, sage_compile = self._resolve_sage_attn()
        variables = {
            "PROMPT": prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            # Z-Image base（capability.zimg_base=True）：非蒸馏需完整 CFG 3-5、steps 28-50
            #（官方推荐），fp8_e4m3fn 加载（16GB 显存装不下 bf16 全精度 → 全黑）。
            # Z-Image Turbo（默认）：蒸馏模型 cfg=1.0，SageAttention 下 steps=8 效果最好。
            "STEPS": self._cap_num("steps", 8),
            "CFG": self._cap_num("cfg", 1.0),
            "WEIGHT_DTYPE": cap.get("weight_dtype", "default"),
            "CLIP": zimg["clip_name"],
            "VAE": zimg["vae_name"], "UNET": zimg["unet_name"],
            # SageAttention 加速（KJNodes PathchSageAttentionKJ）。
            # base 模板不挂该节点（INT8 补丁与 bf16 base 不兼容 → 红马赛克），变量冗余无害
            "SAGE_ATTN": sage_attn, "SAGE_COMPILE": sage_compile,
            # 可选写实/审美 LoRA（2026-09 东方审美接入）：opts.lora_name 优先，否则 capability.lora_name。
            # 仅 Z-Image Turbo（非 zimg_base）启用——base 底模不同构且实测自带画感/噪点，不适合该 Turbo LoRA。
            "LORA": (opts.lora_name if opts.lora_name is not None else cap.get("lora_name")) or "",
            "LORA_STRENGTH": opts.lora_strength if opts.lora_name is not None else self._cap_num("lora_strength", 1.0),
        }
        workflow = self._template("txt2img")
        # 未配置 LoRA 或底模非 Turbo（zimg_base=True）→ 移除 LoRA 节点并把下游 model 重连回上游，
        # 不影响现有生成（与 H3/四视图模板同款逻辑）。
        if not variables["LORA"] or cap.get("zimg_base"):
            _lora_id = next((k for k, v in workflow.items()
                             if v.get("class_type") in ("LoraLoaderModelOnly", "LoraLoaderInt8ConvRot")), None)
            if _lora_id is not None:
                _src = workflow[_lora_id]["inputs"].get("model")
                del workflow[_lora_id]
                for node in workflow.values():
                    if node.get("inputs", {}).get("model") == [_lora_id, 0]:
                        node["inputs"]["model"] = _src
        workflow = self._render(workflow, variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 20),
            meta={"client_id": _client_id},
        )

    def _text_to_image_flux2(self, prompt: str, opts: ImageOpts) -> TaskHandle:
        """Flux.2 Klein 4B 文生图：Klein 模板 0 参考图（纯文生图）。

        采样参数（capability 可覆盖）：steps=20、cfg=5.0（官方 Image Edit blueprint）。
        prompt 由增强链路输出中英双语（bilingual=True），适配英文原生模型。
        """
        width, height = self._size(opts)
        flux2 = self._resolve_flux2_models()
        sage_attn, sage_compile = self._resolve_sage_attn()
        variables = {
            "PROMPT": prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 20),
            "CFG": self._cap_num("cfg", 5.0),
            "CLIP": flux2["clip_name"],
            "VAE": flux2["vae_name"], "UNET": flux2["unet_name"],
            "SAGE_ATTN": sage_attn, "SAGE_COMPILE": sage_compile,
        }
        workflow = self._render(_build_flux2_klein_template(0), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 60),
            meta={"client_id": _client_id},
        )

    def _text_to_image_flux2_9b_gguf(self, prompt: str, opts: ImageOpts) -> TaskHandle:
        """Flux.2 Klein 9B (GGUF Q8_0) 文生图：9B GGUF 模板 0 参考图（纯文生图）。

        官方蒸馏参数（capability 可覆盖）：steps=4、cfg=1.0。负向 ConditioningZeroOut。
        """
        width, height = self._size(opts)
        m = self._resolve_flux2_9b_gguf_models()
        variables = {
            "PROMPT": prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 4),
            "CFG": self._cap_num("cfg", 1.0),
            "CLIP": m["clip_name"],
            "VAE": m["vae_name"], "UNET": m["unet_name"],
        }
        workflow = self._render(_build_flux2_klein_9b_gguf_template(0), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 60),
            meta={"client_id": _client_id},
        )

    def imageToImage(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        cap = self._cap()
        # FLUX.1 dev + XLabs Flux IPAdapter 多图参考（capability.ref_engine=flux_ipadapter）：
        # ImageBatch 拼接参考图（≤3 张，官方 2-3 张最优）→ ApplyFluxIPAdapter 注入。
        # 2026-08-08 部署验证通过（用户实测链路），VAE 用 ae.safetensors。
        if cap.get("ref_engine") == "flux_ipadapter":
            return self._image_to_image_flux_ipadapter(prompt, image_urls, opts)
        # Flux.2 Klein 4B 多图参考编辑（capability.ref_engine=flux2）：
        # 原生支持多图（Kontext 机制），全量注入参考图，不再降级单图。
        if cap.get("ref_engine") == "flux2":
            return self._image_to_image_flux2(prompt, image_urls, opts)
        # Flux.2 Klein 9B (GGUF Q8_0) 多图参考编辑（capability.ref_engine=flux2_9b_gguf）：
        # 与 4B 相同的 ReferenceLatent 链式注入，官方蒸馏 steps=4 / cfg=1.0
        if cap.get("ref_engine") == "flux2_9b_gguf":
            return self._image_to_image_flux2_9b_gguf(prompt, image_urls, opts)
        # Flux.2 Klein 9B (GGUF Q8_0) + CharacterSheet LoRA 四视图
        #（capability.ref_engine=flux2_9b_charsheet，2026-08-09）：封面驱动生成
        # 四格合一四视图（特征格半身不裁切 + 正面/侧面/背面全身）
        if cap.get("ref_engine") == "flux2_9b_charsheet":
            return self._image_to_image_flux2_9b_charsheet(prompt, image_urls, opts)
        # 多图生关键帧（2026-08-07）：角色+场景+道具全部作为参考图 →
        # TextEncodeZImageOmni 原生多图节点（image1/2/3 + VAE 注入 reference_latents）。
        # IPAdapter 为 SD 架构不兼容 Z-Image（Qwen 架构），故采用 ComfyUI 内置原生节点。
        # 2026-08-07 对照实验（4 组视觉 QC + 边缘能量）最终结论：
        #   Z-Image Turbo INT8 蒸馏模型的「多图参考」（≥2 张）必出大马赛克——
        #   多图时参考图 t 位置编码 index=1,2...（qwen_image forward 拼接 token），
        #   超出蒸馏模型训练分布（Turbo 只覆盖单参考 t=1），与参考图尺寸/denoise 无关
        #   （同尺寸 pad 实验同样马赛克）。Omni 单图 + denoise 0.85 质量最优
        #   （实验 D：人物清晰 + 背景由 prompt 重绘为正确场景）。
        #   → 多图一律降级为单图（refs[0]，角色优先），场景/道具一致性由 prompt
        #     文本描述承载（enhanced prompt 含场景资产描述）。
        refs = [u for u in image_urls if u][:3]
        if not refs:
            raise ProviderError("img2img 需要至少一张参考图")
        ups = [self._upload_image(u) for u in refs]
        width, height = self._size(opts)
        zimg = self._resolve_zimage_models()
        sage_attn, sage_compile = self._resolve_sage_attn()
        variables = {
            "PROMPT": prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 8),
            "CFG": self._cap_num("cfg", 1.0),
            "CLIP": zimg["clip_name"],
            "VAE": zimg["vae_name"], "UNET": zimg["unet_name"],
            # SageAttention 加速（KJNodes PathchSageAttentionKJ）
            "SAGE_ATTN": sage_attn, "SAGE_COMPILE": sage_compile,
        }
        # 多图模型已弃用（OmniGen2/Qwen-Edit 均弃用，2026-08-07），Z-Image Turbo 为唯一
        # img2img 模型：多图参考必出马赛克（对照实验），一律降级单图 refs[0]。
        if len(refs) > 1:
            # 非多图模型（Z-Image Turbo 等）：多图参考必出马赛克（2026-08-07 对照实验），
            # 降级为单图参考（refs[0]）
            logger.warning(
                "模型 %s 不支持多图参考（会出马赛克），降级为单图（refs[0]）: %s",
                cap.get("unet_name", ""),
                [u.split("/")[-1] for u in refs],
            )
            refs = refs[:1]
            ups = [ups[0]]
            variables["IMAGE_0"] = ups[0].get("name", "")
            # 固定 d0.85（实验 D 最优）：不读 capability.denoise——那 0.6 是四视图 img2img
            # 模板的历史遗留值，关键帧/降级链路用它会导致保留参考图过多、画面花/马赛克
            variables["DENOISE"] = 0.85
            workflow = self._render(_build_img2img_omni_template(1), variables)
        elif opts.denoise is None:
            # 关键帧/设计图链路（未显式指定 denoise）：走 Omni 单图 + denoise 0.85——
            # 主图 latent 锚定（ImagePadForOutpaintTargetSize 外扩 + VAEEncode）保留 15%
            # 角色细节，85% 重绘为 prompt 场景（对照实验 D 最优：人物清晰 + 背景正确）。
            # 此前多图 Omni（denoise 0.85）实测大马赛克，降级单图后不再注入多余参考。
            variables["IMAGE_0"] = ups[0].get("name", "")
            # 固定 d0.85：capability.denoise=0.6 是四视图 img2img 模板的历史值，
            # 若经 _cap_num 读取会覆盖本链路的最优 0.85 → 画面花/马赛克（实验 B 教训）
            variables["DENOISE"] = 0.85
            workflow = self._render(_build_img2img_omni_template(1), variables)
        else:
            # 四视图（denoise 0.9）/特写（0.45）等显式指定 denoise 的链路：原 img2img 模板
            # （主图锚定 + ImagePadForOutpaintTargetSize 外扩），效果已验证正常，保持不变。
            variables["IMAGE"] = ups[0].get("name", "")
            variables["DENOISE"] = opts.denoise
            workflow = self._render(self._template("img2img"), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 20),
            meta={"client_id": _client_id},
        )

    def _image_to_image_flux_ipadapter(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """FLUX.1 dev (GGUF) + XLabs Flux IPAdapter 多图参考 img2img（ref_engine=flux_ipadapter）。

        参考图上限 3 张（官方 2-3 张最优，超 3 张特征过度混合模糊）；超限报错不静默截断。
        参考图语义标签拼成 "Image N: <label>" 指代块放在 prompt 开头（与 Flux2Klein 一致）。
        负向提示固定附加 no text/no chinese text 压制 FLUX 中文乱码（实测：场景招牌
        重绘乱码"恩大炯茶"→ 负向压制后消除）。
        """
        refs = [u for u in image_urls if u][:3]
        if not refs:
            raise ProviderError("img2img 需要至少一张参考图")
        ups = [self._upload_image(u) for u in refs]
        width, height = self._size(opts)
        flux = self._resolve_flux_ipadapter_models()
        reference_block = ""
        if getattr(opts, "reference_labels", None):
            lines = [
                f"Image {i + 1}: {label}" for i, label in enumerate(opts.reference_labels[: len(ups)])
            ]
            reference_block = "Reference images:\n" + "\n".join(lines) + "\n\n"
        final_prompt = reference_block + prompt
        negative = (opts.negative_prompt or "") + (
            ", no text, no words, no letters, no chinese text, no signage"
        )
        variables = {
            "PROMPT": final_prompt,
            "NEGATIVE": negative,
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 20),
            "CFG": self._cap_num("cfg", 3.5),
            "IP_SCALE": self._cap_num("ip_scale", 0.7),
            "UNET": flux["unet_name"],
            "CLIP1": flux["clip1_name"], "CLIP2": flux["clip2_name"],
            "VAE": flux["vae_name"],
            "IPADAPTER": flux["ipadapter_name"], "CLIP_VISION": flux["clip_vision_name"],
        }
        for i, u in enumerate(ups):
            variables[f"REF_IMAGE_{i}"] = u.get("name", "")
        workflow = self._render(_build_flux_ipadapter_template(len(ups)), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 60),
            meta={"client_id": _client_id},
        )

    def _image_to_image_flux2(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """Flux.2 Klein 4B 多图参考编辑（capability.ref_engine=flux2）。

        全量注入参考图（最多 4 张，Klein 官方上限；官方 Multi-Reference 最佳实践）：
        每张图 LoadImage → 缩到 1MP → VAEEncode → ReferenceLatent 链式累积到正/负
        conditioning。Flux.2 Kontext 机制原生支持多图（ref_latents 循环累积 token），
        不存在 Z-Image Turbo 蒸馏模型的多图马赛克问题。
        采样参数（capability 可覆盖）：steps=20（官方默认）、cfg=5.0（官方 blueprint）。
        """
        refs = [u for u in image_urls if u][:4]
        if not refs:
            raise ProviderError("img2img 需要至少一张参考图")
        ups = [self._upload_image(u) for u in refs]
        width, height = self._size(opts)
        flux2 = self._resolve_flux2_models()
        sage_attn, sage_compile = self._resolve_sage_attn()
        # 多图参考指代（官方 Multi-Reference 最佳实践）：
        # 参考图语义标签（角色/场景/道具名）拼成 "Image N: <label>" 指代块放在 prompt 开头，
        # 让 FLUX.2 知道每张参考图是什么，prompt 正文可用 "the character from Image 1" 指代。
        # 2026-08-08：追加「强制一致性声明」兜底——LLM 增强 prompt 偶尔不写 Image N 指代，
        # 缺指代时参考图沦为弱上下文、角色脸部发散（实测相似度 7 分 vs 4 分）。
        reference_block = ""
        if getattr(opts, "reference_labels", None):
            lines = [
                f"Image {i + 1}: {label}" for i, label in enumerate(opts.reference_labels[: len(ups)])
            ]
            reference_block = (
                "Reference images:\n" + "\n".join(lines) + "\n\n"
                "IMPORTANT: Every character shown in the reference images above MUST appear in "
                "the output with IDENTICAL facial features, hairstyle, and clothing. The image "
                "labeled 'Image N' above is the identity reference for that character/scene. "
                "Do not change, morph, or reinterpret the face, hairstyle, or outfit of any "
                "referenced character.\n\n"
            )
        final_prompt = reference_block + prompt
        variables = {
            "PROMPT": final_prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 20),
            "CFG": self._cap_num("cfg", 5.0),
            "CLIP": flux2["clip_name"],
            "VAE": flux2["vae_name"], "UNET": flux2["unet_name"],
            "SAGE_ATTN": sage_attn, "SAGE_COMPILE": sage_compile,
        }
        for i, u in enumerate(ups):
            variables[f"REF_IMAGE_{i}"] = u.get("name", "")
        workflow = self._render(_build_flux2_klein_template(len(ups)), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 60),
            meta={"client_id": _client_id},
        )

    def _image_to_image_flux2_9b_gguf(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """Flux.2 Klein 9B (GGUF Q8_0) 多图参考编辑（capability.ref_engine=flux2_9b_gguf）。

        与 4B 相同的 ReferenceLatent 链式累积（最多 4 张），区别仅在与模型链路：
        - UnetLoaderGGUF(flux-2-klein-9b-Q8_0.gguf)：绕开 fp8 反量化 bug（彩色噪点）
        - CLIPLoader(qwen_3_8b_fp8mixed, type=flux2) + VAELoader(full_encoder_small_decoder)
        - 官方蒸馏参数 steps=4 / cfg=1.0，负向 ConditioningZeroOut
        """
        refs = [u for u in image_urls if u][:4]
        if not refs:
            raise ProviderError("img2img 需要至少一张参考图")
        ups = [self._upload_image(u) for u in refs]
        width, height = self._size(opts)
        m = self._resolve_flux2_9b_gguf_models()
        reference_block = ""
        if getattr(opts, "reference_labels", None):
            lines = [
                f"Image {i + 1}: {label}" for i, label in enumerate(opts.reference_labels[: len(ups)])
            ]
            # 2026-08-08：除指代列表外追加强制一致性声明——LLM 增强 prompt 偶尔不写
            # Image N 指代（实测第三幕近景/全景缺指代 → 模型脸部发散、不像资产，
            # 同条件第一幕写了「from Image 3」→ 相似度 7 分 vs 4 分）。此声明不依赖
            # LLM 输出，保证参考图角色身份/面部/服装被显式绑定。
            reference_block = (
                "Reference images:\n" + "\n".join(lines) + "\n\n"
                "IMPORTANT: Every character shown in the reference images above MUST appear in "
                "the output with IDENTICAL facial features, hairstyle, and clothing. The image "
                "labeled 'Image N' above is the identity reference for that character/scene. "
                "Do not change, morph, or reinterpret the face, hairstyle, or outfit of any "
                "referenced character.\n\n"
            )
        final_prompt = reference_block + prompt
        variables = {
            "PROMPT": final_prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 4),
            "CFG": self._cap_num("cfg", 1.0),
            "CLIP": m["clip_name"],
            "VAE": m["vae_name"], "UNET": m["unet_name"],
        }
        for i, u in enumerate(ups):
            variables[f"REF_IMAGE_{i}"] = u.get("name", "")
        workflow = self._render(_build_flux2_klein_9b_gguf_template(len(ups)), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 60),
            meta={"client_id": _client_id},
        )

    def _image_to_image_flux2_9b_charsheet(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """Flux.2 Klein 9B (GGUF Q8_0) + CharacterSheet LoRA 四视图（capability.ref_engine=flux2_9b_charsheet）。

        封面驱动的四格合一四视图生成（2026-08-09 验证通过）：
        - 参考图 = 封面（1 张），ReferenceLatent 注入锁定角色身份/服装/配色
        - LoraLoaderModelOnly 挂 CharacterSheet LoRA（capability.lora_name，默认借用
          白名单文件名 ltxv-13b-0.9.7-distilled-lora128.safetensors）
        - 官方蒸馏参数 steps=4/cfg=1.0；尺寸 1536×1024（四视图 R2V 参考图规格，
          由调用方 ImageOpts 指定，_size 只认标准比例 → 直接读 opts.width/height）
        """
        refs = [u for u in image_urls if u][:4]
        if not refs:
            raise ProviderError("img2img 需要至少一张参考图")
        ups = [self._upload_image(u) for u in refs]
        m = self._resolve_flux2_9b_gguf_models()
        width = int(opts.width or 1536)
        height = int(opts.height or 1024)
        variables = {
            "PROMPT": prompt,
            "NEGATIVE": opts.negative_prompt or "",
            "WIDTH": width, "HEIGHT": height,
            "SEED": random.randint(0, 2**32 - 1),
            "STEPS": self._cap_num("steps", 4),
            "CFG": self._cap_num("cfg", 1.0),
            "CLIP": m["clip_name"],
            "VAE": m["vae_name"], "UNET": m["unet_name"],
            "LORA": self._cap().get("lora_name", "ltxv-13b-0.9.7-distilled-lora128.safetensors"),
            "LORA_STRENGTH": self._cap_num("lora_strength", 1.0),
        }
        for i, u in enumerate(ups):
            variables[f"REF_IMAGE_{i}"] = u.get("name", "")
        workflow = self._render(_build_flux2_klein_9b_charsheet_template(len(ups)), variables)
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=self._cap_num("estimated_seconds", 240),
            meta={"client_id": _client_id},
        )


    def imageToVideo(
        self,
        firstFrame: str | None,
        lastFrame: str | None,
        opts: VideoOpts,
        reference_assets: list[str] | None = None,
        reference_videos: list[str] | None = None,
    ) -> TaskHandle:
        cap = self._cap()
        video_kind = cap.get("video_kind", "minimax")  # P8：默认 MiniMax H3（wan2.2/LTX 已下线）
        if video_kind not in ("minimax", "minimax_ref"):
            raise ProviderError(f"生视频仅支持 MiniMax H3（当前 video_kind={video_kind}）")
        up = self._upload_image(firstFrame) if firstFrame else None
        end = None
        if lastFrame:
            # MiniMax H3 原生支持首尾帧（first_frame + last_frame），尾帧=下一镜关键帧
            end = self._upload_image(lastFrame)
        width = opts.width or self._cap_num("width", 832)
        height = opts.height or self._cap_num("height", 480)
        fps = opts.frame_rate or self._cap_num("fps", 16)
        # 帧数对齐：MiniMax H3 17n+5；且不超过 capability.frames_max（防爆显存）
        frames = opts.num_frames or 81
        if video_kind in ("minimax", "minimax_ref"):
            # MiniMax H3 latent 为 16 倍空间下采样且 patch_size=2：
            # 宽高必须是 32 的倍数（如 720x1280 → latent 45x80，45 为奇数无法 patchify，
            # 报 "shape [1,24,...] is invalid for input of size N"）。官方节点 step=32 同理。
            # 2026-08-08：比例优先映射到「capability.minimax_res 档位」——默认 480p
            #（832×480/480×832，提速），后续提升清晰度改 capability.minimax_res="768p"。
            # 严格按档位基准执行，避免比例失真。
            grade = str(cap.get("minimax_res", "0.7mp"))
            grade_map = _MMAX_RES_GRADES.get(grade, _MMAX_RES_GRADES["0.7mp"])
            ratios = []
            if abs(width / height - 16 / 9) <= 0.06:
                ratios.append("16:9")
            if abs(width / height - 9 / 16) <= 0.06:
                ratios.append("9:16")
            if abs(width / height - 4 / 3) <= 0.06:
                ratios.append("4:3")
            if abs(width / height - 3 / 4) <= 0.06:
                ratios.append("3:4")
            if abs(width / height - 1) <= 0.06:
                ratios.append("1:1")
            if ratios:
                width, height = grade_map[ratios[0]]
            else:
                width = ((width + 31) // 32) * 32
                height = ((height + 31) // 32) * 32
            frames = max(5, (frames - 5) // 17 * 17 + 5)
            # AI 视频页签短视频档位：显式 min_frames 时允许低于默认下限
            #（如 4s=96 帧），由 H3 节点按 17n+5 网格向上对齐；否则保持 124 帧下限
            min_frames = opts.min_frames if opts.min_frames is not None else 124
            frames = max(min_frames, frames)
            frames = min(frames, self._cap_num("frames_max", 362))
        default_models = _default_minimax_models()
        models = {**default_models, **{k: v for k, v in cap.items() if k in default_models}}
        # 风格适配：SigmaShift 双流 shift 与步数（opts 优先，回退 capability/模板默认）
        shift_video = opts.shift_video if opts.shift_video is not None else cap.get("shift_video", 12.0)
        shift_audio = opts.shift_audio if opts.shift_audio is not None else cap.get("shift_audio", 3.0)
        # P8：Turbo 蒸馏 LoRA（v1.1 bf16 comfyui，LoraLoaderModelOnly 加载）。音频修复 2026-08-07：steps=8（turbo 档位），
        # 4 步下音频 latent 未收敛 → 撕裂电音；8 步画面清晰且音频正常（比 4 步慢约 2 倍）
        steps = opts.steps or self._cap_num("steps", 8)
        # 2026-08-22 可配置参数：用户显式设定（三列工作台右侧面板）优先于能力表默认
        seed_val = opts.seed if opts.seed is not None else random.randint(0, 2**32 - 1)
        cfg_val = opts.cfg if opts.cfg is not None else self._cap_num("cfg", 1.0)
        # 2026-09-16：蒸馏/Turbo 模型（能力表 cfg==1.0）→ CFG 强制 1.0（蒸馏模型 CFG>1 无效，产糊/撕裂），忽略用户高 cfg
        _cap_cfg = cap.get("cfg")
        try:
            if _cap_cfg is not None and float(_cap_cfg) == 1.0:
                cfg_val = 1.0
        except (TypeError, ValueError):
            pass
        variables = {
            "PROMPT": opts.prompt or "",
            "NEGATIVE": cap.get("negative_prompt", ""),
            "WIDTH": width, "HEIGHT": height,
            "FRAMES": frames, "FPS": fps,
            "SEED": seed_val,
            "STEPS": steps,
            "CFG": cfg_val,
            "SHIFT_VIDEO": shift_video, "SHIFT_AUDIO": shift_audio,
            "UNET": models["unet_name"],
            "CLIP": models["clip_name"],
            "VAE": models["vae_name"],
            "VAE_AUDIO": models.get("vae_audio_name", ""),
            "LORA": models.get("lora_name", ""),
        }
        # 2026-08-09：首帧可空（分镜级资产参考链路无首帧）——仅当存在首帧时注入
        # IMAGE 变量（minimax 模板需要）；minimax_ref 模板用 REF_IMAGE_N 不用 IMAGE。
        if up is not None:
            variables["IMAGE"] = up.get("name", "")
        workflow_template = None
        if video_kind == "minimax_ref":
            # P6 Phase2 R2V：多模态参考（角色四视图/场景封面等），对标 Seedance 多锚点做法。
            # R2V 使用独立 ref2va unet 权重（capability.unet_name 指定）。
            # 2026-08-09（分镜级直接用资产图出片）：首帧不再强制占位——
            # 有首帧（幕级/关键帧链路兼容）时作 ref_image_0 锚定构图，
            # 无首帧（分镜级资产参考链路）时 reference_assets 自身即为参考图序列，
            # 其第一张（场景 cover）作 ref_image_0 构图锚点。
            # 2026-08-11（AI 视频页签）：支持参考视频 + 参考图混合（reference_videos），
            # 对应节点 ref_videos（≤3）/ ref_video_audios 输入，LoadVideo 上传。
            refs = []
            if up is not None:
                refs.append(up)
            refs += [self._upload_image(u) for u in (reference_assets or []) if u]
            refs = refs[:9]
            videos = []
            if reference_videos:
                videos = [self._upload_video(v) for v in reference_videos if v]
                videos = videos[:3]
            if not refs and not videos:
                raise ProviderError(
                    "MiniMax H3 R2V 需要至少一张参考图或一个参考视频"
                )
            ref_image_size = str(cap.get("ref_image_size", "match"))
            if ref_image_size not in ("match", "max"):
                ref_image_size = "match"
            use_sla = str(cap.get("sla", "")).lower() in ("1", "true", "yes")
            workflow_template = _build_minimax_ref_template(
                len(refs), ref_image_size, len(videos),
                use_sol=str(cap.get("sol_attention", "")).lower() in ("1", "true", "yes"),
                use_sla=use_sla,
            )
            variables.update({f"REF_IMAGE_{i}": up["name"] for i, up in enumerate(refs)})
            variables.update({f"REF_VIDEO_{i}": v["name"] for i, v in enumerate(videos)})
            # P8：未配置 turbo LoRA 时，移除 LoraLoader(LoraLoaderInt8ConvRot/LoraLoaderModelOnly) 节点，
            # 并把原来接住 LoRA 的节点（sol 补丁 或 SigmaShift）重新接到 UNET 输出（node 1）
            if not models.get("lora_name"):
                _lora_id = next((k for k, v in workflow_template.items()
                                 if v.get("class_type") in ("LoraLoaderInt8ConvRot", "LoraLoaderModelOnly")), None)
                if _lora_id is not None:
                    _src = workflow_template[_lora_id]["inputs"].get("model")
                    del workflow_template[_lora_id]
                    for node in workflow_template.values():
                        if node.get("inputs", {}).get("model") == [_lora_id, 0]:
                            node["inputs"]["model"] = _src
        else:
            workflow_template = self._template("img2vid")
            # 2026-08-11（AI 视频页签纯文生）：无首帧时移除 LoadImage 节点（id=5）与
            # MiniMaxH3ImageToVideo.first_frame 输入——否则 __IMAGE__ 占位符未替换，
            # ComfyUI 校验报「Invalid image file: __IMAGE__」。first_frame 是 optional 输入，
            # 纯文生（T2V）不接即可。
            if up is None:
                h3_nodes = [
                    n for n in workflow_template.values()
                    if n.get("class_type") == "MiniMaxH3ImageToVideo"
                ]
                for n in h3_nodes:
                    n["inputs"].pop("first_frame", None)
                workflow_template = {
                    k: v for k, v in workflow_template.items()
                    if v.get("class_type") != "LoadImage"
                }
            if end and video_kind == "minimax":
                # P6 首尾帧：有尾帧（下一镜关键帧）时注入 last_frame 节点
                workflow_template = _inject_last_frame(workflow_template, end["name"])
            # P8：未配置 turbo LoRA（models 无 lora_name 或 capability 显式置空）时，
            # 移除 LoraLoader 节点并恢复 SigmaShift 的 model 引用 SageAttention 补丁输出（node 42）
            if video_kind == "minimax" and not models.get("lora_name"):
                _lora_id = next((k for k, v in workflow_template.items()
                                    if v.get("class_type") in ("LoraLoaderInt8ConvRot", "LoraLoaderModelOnly")), None)
                if _lora_id is not None:
                    _src = workflow_template[_lora_id]["inputs"].get("model")
                    del workflow_template[_lora_id]
                    for node in workflow_template.values():
                        if node.get("inputs", {}).get("model") == [_lora_id, 0]:
                            node["inputs"]["model"] = _src
        # P9：Spectrum 谱特征预测加速（跳过部分 transformer 步骤，~30%）。
        # capability.spectrum="false" 或服务器未安装该节点时自动回退原生采样。
        if video_kind in ("minimax", "minimax_ref") and str(cap.get("spectrum", "true")).lower() != "false":
            if self._node_available("SpectrumApplyMiniMaxH3"):
                workflow_template = _apply_spectrum(workflow_template)
            else:
                logger.info("SpectrumApplyMiniMaxH3 未安装，回退原生采样")
        # P10（2026-09-01）：无对白/旁白镜头（纯画面）关闭 H3 原生音轨——H3 AV 在无台词
        # 指令时仍会凭空生成失真人声（【纯画面镜头】提示词压不住，见 generate_video.py
        # 纯视觉坑注）。移除 VAEDecodeAudio 节点 + CreateVideo.audio 输入 → 成片静音，
        # 最终声音由 BGM/SFX/配音层供给。有对白镜头 native_audio=True 保留原生音频。
        if not opts.native_audio and video_kind in ("minimax", "minimax_ref"):
            # ⚠️ 必须 deepcopy：_template("img2vid") 返回模块级共享常量，直接 mutate
            # 会把全局模板的 CreateVideo.audio 删掉，毒化后续所有 native_audio=True 的音轨。
            import copy
            workflow_template = copy.deepcopy(workflow_template)
            workflow_template = {
                k: v for k, v in workflow_template.items()
                if v.get("class_type") != "VAEDecodeAudio"
            }
            for v in workflow_template.values():
                if v.get("class_type") == "CreateVideo":
                    v.setdefault("inputs", {}).pop("audio", None)
        workflow = self._render(workflow_template, variables)
        prompt_id, _client_id = self._submit(workflow)
        duration = opts.duration or (frames / fps if fps else 5.0)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=int(duration * 2.5),
            meta={"duration": duration, "client_id": _client_id},
        )

    def upscaleVideo(
        self,
        src_video_name: str,
        tier: str = "4x",
        target_width: int = 1920,
        target_height: int = 1080,
        src_fps: float = 24.0,
        duration: float | None = None,
        prefix: str = "upscale",
    ) -> TaskHandle:
        """提交视频超分工作流（VHS_LoadVideo → 逐帧 SR → 归一到目标分辨率 → mp4）。

        src_video_name：已上传到 ComfyUI input 目录的视频文件名（_upload_video / 任务内
        直传均可）。tier: "4x"→4x-UltraSharp（带抑晕）/ "2x"→RealESRGAN_x2plus。
        音频不随超分输出，由 upscale_video 任务下载后本地混回原音轨并做帧数/时长校验。
        """
        workflow_template = _build_upscale_template(
            tier, target_width, target_height, src_fps, prefix
        )
        workflow = self._render(workflow_template, {"VIDEO": src_video_name})
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=int(max(60, 2 * 60)),
            meta={"duration": duration, "client_id": _client_id},
        )

    def refineVideo(
        self,
        src_video_name: str,
        prompt: str,
        negative: str = "",
        steps: int = 8,
        denoise: float = 0.22,
        ic_lora_strength: float = 1.0,
        prefix: str = "refine",
    ) -> TaskHandle:
        """提交 LTX-2.5「原生分辨率精修（只精修不放大）」工作流（2026-08-27）。

        src_video_name：已上传到 ComfyUI input 目录的视频文件名。
        E2A 实测定案参数：8 步 / denoise 0.22 / IC-LoRA 强度 1.0 / euler+simple / cfg 1.0，
        不放大、无 guide（踩坑库 41）。音频由 VHS_VideoCombine 回带原音轨。
        """
        workflow_template = _build_ltx_refine_template(
            prompt, negative, steps=steps, denoise=denoise,
            ic_lora_strength=ic_lora_strength, prefix=prefix,
        )
        workflow = self._render(workflow_template, {
            "VIDEO": src_video_name, "PROMPT": prompt, "NEGATIVE": negative,
        })
        prompt_id, _client_id = self._submit(workflow)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=int(max(60, 2 * 60)),
            meta={"client_id": _client_id},
        )


    def _upload_audio(self, audio_url: str) -> dict:
        """把本地 media URL 音频上传到 ComfyUI input 目录，返回 {name, subfolder, type}。

        供导演台参考音频（refAudios -> <Audio N>）使用；仅支持本地 /static/media/ URL。
        ComfyUI /upload/image 不校验内容类型，音频文件按原样落盘 input 目录。
        """
        marker = "/static/media/"
        if marker not in audio_url:
            raise ProviderError(
                f"ComfyUI 参考音频必须是本地 media URL（当前: {audio_url[:80]}）"
            )
        import os
        from app.config import settings as _s
        local_path = os.path.join(_s.media_dir, audio_url.split(marker, 1)[1])
        if not os.path.exists(local_path):
            raise ProviderError(f"参考音频文件不存在: {local_path}")
        rel = audio_url.split(marker, 1)[1]
        safe_name = rel.replace("/", "__").replace("\\", "__")
        with open(local_path, "rb") as f:
            files = {"image": (safe_name, f, "audio/mpeg")}
            data = {"overwrite": "true", "type": "input"}
            r = self.http.post(
                f"{self._endpoint()}/upload/image", files=files, data=data, timeout=120
            )
        r.raise_for_status()
        return r.json()  # {name, subfolder, type}

    def director_node_available(self) -> bool:
        """探测 166 是否已安装 MiniMaxH3Director 节点插件。"""
        return self._node_available("MiniMaxH3Director")

    def director_generate(
        self,
        timeline_data: str,
        *,
        task_type: str = "r2v — 参考主体生视频(Reference to Video)",
        global_prompt: str = "",
        width: int = 832,
        height: int = 480,
        ref_max_size: int = 864,
        total_frames: int = 124,
        frame_rate: float = 24.0,
        steps: int | None = None,
        sampler: str = "res_multistep",
        scheduler: str = "simple",
        cfg: float | None = None,
        seed: int | None = None,
        shift_video: float | None = None,
        shift_audio: float | None = None,
        high_quality: bool = False,
    ) -> TaskHandle:
        """提交 MiniMaxH3Director 多段连续生视频工作流。
        
        high_quality=True 时默认用更高保真档（步数升到 16，默认 8），
        用于"成片/高质量"；该链路不应用外部 Spectrum（提速走步数/档位）。

        timeline_data：导演台时间轴 JSON 字符串。其 global.refs 条目中的 imageFile
        若为本地 media URL（/static/media/…）会在提交前上传到 166 input/ 并替换为
        文件名（与 R2V 参考图同链路）；上传失败（166 离线/文件缺失）跳过该引用。
        """
        import json  # noqa: F401

        if not self._node_available("MiniMaxH3Director"):
            raise ProviderError(
                "166 ComfyUI 未安装 MiniMax H3 Director 插件，导演台模式不可用。"
                "请先在 166 安装 AIMixer/ComfyUI_MiniMaxH3_Director，"
                "或配置 DIRECTOR_MODE=mock 在离线态开发验证。"
            )
        cap = self._cap()
        # 公共参考图：URL → 上传 166 input/ → 替换为文件名（缺图/上传失败跳过）
        tl = json.loads(timeline_data)
        g = tl.get("global") or {}
        refs = g.get("refs") or []
        replaced = 0
        skipped = 0
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            url = str(ref.get("imageFile") or "").strip()
            if not url or url.startswith("__"):
                continue
            try:
                up = self._upload_image(url)
                ref["imageFile"] = up.get("name", "")
                replaced += 1
            except Exception as exc:  # noqa: BLE001 单条失败不阻断整段提交
                skipped += 1
                logger.warning("导演台参考图上传失败，跳过该引用(%s): %s", url[:60], exc)
                ref["imageFile"] = ""
        for i, ref in enumerate(refs):
            if isinstance(ref, dict):
                ref.setdefault("index", i)
        if replaced or skipped:
            logger.info("导演台参考图: 成功 %d 张, 跳过 %d 张", replaced, skipped)
        # 参考音频（refAudios -> <Audio N>）：本地 URL 上传 166 input/ 并替换为文件名
        audios = g.get("refAudios") or []
        for i, ref in enumerate(audios):
            if not isinstance(ref, dict):
                continue
            url = str(ref.get("audioFile") or "").strip()
            if not url or url.startswith("__"):
                continue
            try:
                up = self._upload_audio(url)
                ref["audioFile"] = up.get("name", "")
                logger.info("导演台参考音频已上传: %s", up.get("name", ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning("导演台参考音频上传失败，跳过该引用(%s): %s", url[:60], exc)
                ref["audioFile"] = ""
        timeline_data = json.dumps(tl, ensure_ascii=False)

        default_models = _default_minimax_models()
        models = {**default_models, **{k: v for k, v in cap.items() if k in default_models}}
        shift_video = shift_video if shift_video is not None else cap.get("shift_video", 12.0)
        shift_audio = shift_audio if shift_audio is not None else cap.get("shift_audio", 3.0)
        steps_val = steps if steps is not None else (16 if high_quality else self._cap_num("steps", 8))
        seed_val = seed if seed is not None else random.randint(0, 2**32 - 1)
        cfg_val = cfg if cfg is not None else self._cap_num("cfg", 1.0)
        variables = {
            "TIMELINE": timeline_data,
            "GLOBAL_PROMPT": global_prompt or "",
            "WIDTH": width, "HEIGHT": height, "REF_MAX": ref_max_size,
            "TOTAL_FRAMES": total_frames, "FPS": int(frame_rate or 24),
            "STEPS": steps_val, "SAMPLER": sampler, "SCHEDULER": scheduler,
            "CFG": cfg_val, "SEED": seed_val,
            "SHIFT_VIDEO": shift_video, "SHIFT_AUDIO": shift_audio,
            "UNET": models["unet_name"], "CLIP": models["clip_name"],
            "VAE": models["vae_name"], "VAE_AUDIO": models.get("vae_audio_name", ""),
            "LORA": models.get("lora_name", ""),
        }
        workflow_template = _build_minimax_h3_director_template(task_type=task_type)
        # P8 同款：未配置 turbo LoRA 时移除 LoRA 节点并重连主干
        if not models.get("lora_name"):
            _lora_id = next((k for k, v in workflow_template.items()
                             if v.get("class_type") in ("LoraLoaderInt8ConvRot", "LoraLoaderModelOnly")), None)
            if _lora_id is not None:
                _src = workflow_template[_lora_id]["inputs"].get("model")
                del workflow_template[_lora_id]
                for node in workflow_template.values():
                    if node.get("inputs", {}).get("model") == [_lora_id, 0]:
                        node["inputs"]["model"] = _src
        workflow = self._render(workflow_template, variables)
        prompt_id, _client_id = self._submit(workflow)
        duration = total_frames / max(int(frame_rate or 24), 1)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=prompt_id,
            pollUrl=f"{self._endpoint()}/history/{prompt_id}",
            estimatedSeconds=int(duration * 2.5 * max(1, 60)),
            meta={"duration": duration, "client_id": _client_id},
        )

    @staticmethod
    def _size(opts: ImageOpts) -> tuple[int, int]:
        """根据 ratio 映射宽高（默认 768p 基准：长边 1344 / 短边 768，与视频统一）。

        Z-Image Turbo 基于 Qwen-Image，原生训练 1024；768p 基准与图生视频统一，
        便于首尾帧衔接时尺寸对齐。16 倍数对齐保证 VAE 编解码无尺寸错误。
        opts.base=1024 时 1:1（角色/道具封面、四视图）升到 1024×1024 原生最优档；
        其他比例（关键帧/场景封面）保持 768p 基准，与视频对齐。
        """
        ratio = (opts.ratio or "16:9").replace("：", ":")
        w, h = 768, 768
        if ratio == "16:9":
            w, h = 1344, 768
        elif ratio == "9:16":
            w, h = 768, 1344
        elif ratio == "4:3":
            w, h = 1024, 768
        elif ratio == "3:4":
            w, h = 768, 1024
        elif ratio == "2:3":
            # 2026-08-24 剧本海报规格：竖版 2:3 电影海报（768×1152，16 倍数对齐 VAE）。
            # 此前 ratio=2:3 无分支落入默认 768×768 方形，海报缩略图被容器裁剪。
            w, h = 768, 1152
        elif ratio == "3:2":
            w, h = 1152, 768
        elif ratio == "1:1":
            # 1024 档：1:1 封面/四视图用原生最优分辨率（约 1M 像素）
            w, h = (1024, 1024) if (opts.base or 768) >= 1024 else (768, 768)
        # 取 16 的倍数（VAE 对齐；768p/1024p 档位已是 16 的倍数）
        w = w // 16 * 16
        h = h // 16 * 16
        return w, h

    # ─── 轮询结果 ─────────────────────────────────────────────────

    def getTaskResult(self, handle: TaskHandle) -> TaskResult:
        try:
            r = self.http.get(handle.pollUrl, timeout=30)
            r.raise_for_status()
        except Exception as e:
            # 网络抖动 → 视为仍在处理
            return TaskResult(status=ProviderStatus.running, raw={"err": str(e)})

        data = r.json()
        entry = data.get(handle.providerTaskId) or {}
        if not entry:
            # history 查无记录：可能是任务仍在队列（未完成），也可能是记录已被
            # 清理（远程早已结束，卡死探测必须区分，否则永远无法回收）。
            if self._in_queue(handle.providerTaskId):
                return TaskResult(status=ProviderStatus.running, raw=data)
            # 2026-08-12 竞态修复：任务完成瞬间存在 history 落盘延迟——
            # queue 已移除但 /history 尚未返回该任务（实测任务已成功出图却被误判
            # failed）。立即判 failed 会误杀刚成功的任务，须重试窗口确认。
            for attempt in range(3):
                time.sleep(1 + attempt)  # 1s → 2s → 3s
                try:
                    r2 = self.http.get(handle.pollUrl, timeout=30)
                    r2.raise_for_status()
                    data2 = r2.json()
                except Exception:
                    continue
                entry2 = data2.get(handle.providerTaskId) or {}
                if entry2:
                    entry = entry2
                    data = data2
                    break
            if not entry:
                return TaskResult(
                    status=ProviderStatus.failed,
                    error="远程任务记录已丢失（ComfyUI history 已清理或任务从未执行）",
                    raw=data,
                )
        status = entry.get("status") or {}
        status_str = status.get("status_str", "")
        completed = bool(status.get("completed"))

        # 失败判定：status_str=error 或 messages 含执行错误
        messages = status.get("messages") or []
        errors = [m for m in messages if isinstance(m, dict) and m.get("type") in ("execution_error", "execution_interrupted")]
        if status_str == "error" or errors:
            detail = ""
            for m in errors:
                if m.get("type") == "execution_interrupted":
                    detail = "任务被中断或取消（ComfyUI 执行被打断）"
                    break
                msg = m.get("message") or {}
                if isinstance(msg, dict):
                    detail = msg.get("message") or msg.get("exception_message") or str(msg)
                else:
                    detail = str(msg)
                break
            if not detail and messages:
                detail = str(messages[-1])
            return TaskResult(status=ProviderStatus.failed, error=detail or "ComfyUI 执行失败", raw=data)

        if not completed:
            return TaskResult(status=ProviderStatus.running, raw=data)

        # 提取输出文件：按扩展名区分图片/视频（SaveVideo 的 mp4 也挂在 images 键下）
        _VIDEO_EXTS = (".mp4", ".webm", ".mov", ".avi", ".gif")
        _IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
        outputs = entry.get("outputs") or {}
        video_files = []
        image_files = []
        for node_out in outputs.values():
            for key in _OUTPUT_KEYS:
                for f in (node_out.get(key) or []):
                    if not isinstance(f, dict) or not f.get("filename"):
                        continue
                    url = self._view_url(f)
                    fname = (f.get("filename") or "").lower()
                    if fname.endswith(_VIDEO_EXTS):
                        video_files.append(url)
                    elif fname.endswith(_IMAGE_EXTS):
                        image_files.append(url)
        if video_files:
            return TaskResult(
                status=ProviderStatus.succeeded,
                videoUrl=video_files[0],
                duration=handle.meta.get("duration"),
                raw=data,
            )
        if image_files:
            return TaskResult(
                status=ProviderStatus.succeeded,
                imageUrls=image_files,
                raw=data,
            )
        return TaskResult(status=ProviderStatus.running, raw=data)

    def _view_url(self, f: dict) -> str:
        from urllib.parse import urlencode
        q = urlencode({
            "filename": f.get("filename", ""),
            "subfolder": f.get("subfolder", ""),
            "type": f.get("type", "output"),
        })
        return f"{self._endpoint()}/view?{q}"

    def test_connection(self) -> tuple[bool, str]:
        try:
            r = self.http.get(f"{self._endpoint()}/system_stats", timeout=15)
            r.raise_for_status()
            stats = r.json()
            ver = stats.get("system", {}).get("comfyui_version", "?")
            return True, f"ComfyUI {ver} 在线"
        except Exception as e:
            return False, map_to_chinese(e)
