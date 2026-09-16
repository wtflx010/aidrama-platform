from pydantic import BaseModel


class UpscaleRequest(BaseModel):
    """超分请求体：tier=4x（默认，4x-UltraSharp+抑晕）/ 2x（RealESRGAN_x2plus 快速档）。
    两档输出均归一标准 1080p（2026-08-23 起，2x 此前为 2× 原生 960p）。"""
    tier: str = "4x"
