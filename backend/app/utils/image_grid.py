"""图片网格拼接工具：用 PIL 把多张图合成单图，供 img2img 单图传入。

img2img 只接受单张图，多角色分镜需把多个角色参考图拼成一张。
"""
import base64
import io
import math
import os
import uuid

import httpx
from PIL import Image

from app.config import settings
from app.utils.media import _MARKER

_CELL = (768, 768)  # 每格尺寸


def _load_image(url: str) -> Image.Image | None:
    """从 URL/data URI 加载 PIL Image。

    - data URI → base64 解码
    - 本地 media URL（含 /static/media/）→ 读文件
    - 公网 URL → httpx 下载
    """
    if not url:
        return None
    # data URI
    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            return Image.open(io.BytesIO(base64.b64decode(b64)))
        except Exception:
            return None
    # 本地 media URL
    if _MARKER in url:
        local_path = os.path.join(settings.media_dir, url.split(_MARKER, 1)[1])
        if not os.path.exists(local_path):
            return None
        try:
            return Image.open(local_path)
        except Exception:
            return None
    # 公网 URL
    try:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))
    except Exception:
        return None


def stitch_grid(urls: list[str], cols: int = 2, max_images: int = 4) -> str | None:
    """把多张图拼接为 cols 列网格图，保存到 media_dir/assets/_grid/，返回本地 media URL。

    - 空列表 → None
    - 单张 → 原样返回该 URL（不拼接）
    - 多张 → 按 cols 列铺满 rows 行；超过 max_images 张时取前 max_images 张
      （默认 4：多角色分镜主角优先；场景多视角六视图可传 max_images=6）
    """
    urls = [u for u in urls if u][:max_images]
    if not urls:
        return None
    if len(urls) == 1:
        return urls[0]

    rows = math.ceil(len(urls) / cols)
    canvas = Image.new("RGB", (cols * _CELL[0], rows * _CELL[1]), (255, 255, 255))

    for i, url in enumerate(urls):
        img = _load_image(url)
        if img is None:
            continue
        img = img.convert("RGB").resize(_CELL, Image.LANCZOS)
        r, c = divmod(i, cols)
        canvas.paste(img, (c * _CELL[0], r * _CELL[1]))

    out_dir = os.path.join(settings.media_dir, "assets", "_grid")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    out_path = os.path.join(out_dir, filename)
    # 带 sRGB ICC 保存：广色域屏下浏览器与本地预览色彩一致（2026-08-10）
    from app.utils.media import save_png_srgb

    save_png_srgb(canvas, out_path)
    return f"{settings.static_base_url}/media/assets/_grid/{filename}"
