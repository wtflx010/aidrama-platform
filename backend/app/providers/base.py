"""Provider 适配器基类与共享数据结构。"""
import enum

import httpx
from pydantic import BaseModel

from app.config import settings
from app.utils.secrets import resolve_key


class ProviderStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskHandle(BaseModel):
    provider: str
    providerTaskId: str
    pollUrl: str | None = None
    estimatedSeconds: int | None = None
    meta: dict = {}  # 同步适配器缓存即时结果


class TaskResult(BaseModel):
    status: ProviderStatus
    imageUrls: list[str] = []
    videoUrl: str | None = None
    duration: float | None = None
    audioUrl: str | None = None
    raw: dict = {}
    error: str | None = None


class ImageOpts(BaseModel):
    size: str | None = None  # Agnes: "1K"/"2K"/"3K"/"4K"
    ratio: str | None = None  # "16:9" 等
    n: int = 1
    # Agnes 文档未列出但实测支持（2026-07 直连验证 HTTP 200）
    negative_prompt: str | None = None
    # ComfyUI img2img 重绘强度（None 走 capability.denoise，默认 0.6）
    # 四视图等需保持角色一致性的场景传更低值（如 0.45），保留更多参考图细节
    denoise: float | None = None
    # ComfyUI 分辨率档位（None=768p 基准，1024=1024p 基准）。
    # 角色/道具封面与四视图（1:1）走 1024×1024 原生最优档（Z-Image 约 1M 像素）；
    # 关键帧/场景封面保持 768p 基准，与图生视频 768p 对齐（2026-08-07）
    base: int | None = None
    # 多图参考指代标签（与 image_urls 一一对应）：如 ["角色「周远」", "场景「雨夜街道」"]，
    # Flux.2 Klein 等原生多图模型据此生成 "Image N: <label>" 指代块（官方 Multi-Reference 最佳实践）
    reference_labels: list[str] | None = None
    # 显式输出宽高（px）。默认 None 由 provider 按 ratio/base 推导；四视图
    # CharacterSheet 链路需 1536×1024（R2V 参考图规格）等非标准比例时显式指定
    width: int | None = None
    height: int | None = None
    # 文生图 LoRA（2026-09 东方审美接入）：None 走 capability.lora_name，显式设置覆盖。
    # 仅写实角色封面等需要时由调用方传入；未配置时 provider 自动去掉 LoRA 节点并
    # 重连主干，不影响现有生成。
    lora_name: str | None = None
    lora_strength: float = 1.0


class VideoOpts(BaseModel):
    prompt: str
    width: int = 1280
    height: int = 720
    num_frames: int = 121  # 必须 8n+1，≤441
    frame_rate: int = 24
    duration: float | None = None
    # P4 提示词精细化：负面提示词（ComfyUI 模板的 __NEGATIVE__；Agnes 忽略）
    negative_prompt: str | None = None
    # MiniMax H3 风格适配：SigmaShift 双流 shift + 采样步数（None 走 capability/模板默认）
    shift_video: float | None = None
    shift_audio: float | None = None
    steps: int | None = None
    # 2026-08-22 可配置生成参数（三列工作台右侧面板透传）：None = 模型默认
    cfg: float | None = None
    seed: int | None = None
    # AI 视频页签短视频档位（4/5/8/10/15 秒）：帧数下限。
    # None 走 provider 默认下限（H3 124 帧）；显式传值则允许低于默认下限的短时长
    #（如 4s=96 帧），由 H3 节点按 17n+5 网格自动向上对齐
    min_frames: int | None = None
    # 2026-09-01 原生音轨开关：minimax/minimax_ref 是否保留 H3 原生音频链
    #（VAEDecodeAudio → CreateVideo.audio）。纯画面（无对白/旁白）镜头置 False →
    # 成片无音轨（静音），避免 H3 AV 凭空生成失真人声；有对白/旁白的镜头为 True。
    native_audio: bool = True


class TTSOpts(BaseModel):
    voice: str = "default"
    speed: float = 1.0
    response_format: str = "wav"  # wav/mp3/opus/aac/flac
    # 差异化配音：情绪标签 + CosyVoice instruct 自然语言指令 + 音高微调
    emotion: str | None = None
    instruct_text: str | None = None
    pitch: float | None = None
    # CosyVoice zero_shot 一次性参考音频（优先于 voice 字段，无需预注册）
    prompt_wav: str | None = None
    prompt_text: str | None = None


def build_httpx_client(proxy: str | None = None) -> httpx.Client:
    p = proxy or settings.https_proxy
    # trust_env=False：不读取系统代理环境变量，避免 ICUBE_PROXY_HOST 等非标准代理干扰
    return httpx.Client(
        timeout=httpx.Timeout(120.0),
        proxy=p if p else None,
        trust_env=False,
    )


class BaseProvider:
    provider_type: str = ""

    def __init__(self, model_config):
        self.cfg = model_config
        self.api_key = resolve_key(model_config.api_key_ref)
        self.http = build_httpx_client()

    def _auth(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def textToImage(self, prompt: str, opts: ImageOpts) -> TaskHandle:
        raise NotImplementedError

    def imageToImage(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """图生图/多图合成：以 image_urls（场景/角色/道具参考图）为参考生成新图。

        多图：Agnes(agnes-image-2.0-flash) 原生支持 image 数组多图合成；
        ComfyUI 单图 latent 实现将多图拼接为一张。
        """
        raise NotImplementedError

    def imageToVideo(
        self,
        firstFrame: str,
        lastFrame: str | None,
        opts: VideoOpts,
        reference_assets: list[str] | None = None,
    ) -> TaskHandle:
        """图生视频：首帧（+可选尾帧）→ 视频。

        reference_assets（P6 Phase2 预留）：多模态参考素材（角色四视图/场景封面/上一镜画面），
        对标 Seedance 多锚点做法；仅支持多参考的模型（如 MiniMax H3 R2V）消费，其余忽略。
        """
        raise NotImplementedError

    def cancel(self, provider_task_id: str) -> None:
        """取消远程生成任务（best-effort）。不支持取消的 provider 默认 no-op。"""
        pass

    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> TaskHandle:
        """TTS 语音合成：文本 → 音频。"""
        raise NotImplementedError

    def getTaskResult(self, handle: TaskHandle) -> TaskResult:
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str]:
        return False, "未实现"
