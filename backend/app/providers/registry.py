"""按 Model 配置实例化对应 Provider 适配器。"""
from sqlalchemy.orm import Session

from app.models.model_config import Model, ProviderType
from app.providers.base import BaseProvider
from app.providers.comfyui import ComfyUIProvider
from app.providers.errors import ProviderError
from app.providers.http_poll import HttpPollProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.openai_tts import OpenAITTSProvider


class ProviderRegistry:
    _registry = {
        ProviderType.openai_compatible: OpenAICompatibleProvider,
        ProviderType.http_poll: HttpPollProvider,
        ProviderType.openai_tts: OpenAITTSProvider,
        ProviderType.comfyui: ComfyUIProvider,
    }

    @classmethod
    def for_model(cls, model: Model, resolution: str | None = None) -> BaseProvider:
        """实例化 provider。resolution（"480p"/"720p"/"768p"）：项目级视频分辨率，
        仅对 ComfyUI 视频模型生效（覆盖 minimax_res 档位，2026-08-16）。
        """
        try:
            pt = ProviderType(model.provider_type)
        except ValueError:
            raise ProviderError(f"未支持的 provider_type={model.provider_type}")
        klass = cls._registry.get(pt)
        if not klass:
            raise ProviderError(f"未注册的 provider_type={pt}")
        provider = klass(model_config=model)
        if resolution and isinstance(provider, ComfyUIProvider):
            r = (resolution or "").strip().lower()
            # 仅放行已知档位；HD/None/未知保持 capability 默认（否则会 fallback 到错误档位）
            if r in ("480p", "720p", "768p"):
                provider._res_override = r
        return provider

    @classmethod
    def for_model_id(cls, db: Session, model_id) -> BaseProvider:
        m = db.get(Model, model_id)
        if not m or not m.is_enabled:
            raise ProviderError("模型不存在或已停用")
        return cls.for_model(m)