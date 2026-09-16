"""image_grid 图片拼接工具测试。

不需要数据库，用临时文件测试 PIL 拼接逻辑。
"""
import os

from PIL import Image

from app.utils.image_grid import stitch_grid, _load_image


def _make_test_image(path: str, color: tuple = (255, 0, 0)):
    """创建一张测试图片。"""
    img = Image.new("RGB", (100, 100), color)
    img.save(path, "PNG")


def test_stitch_grid_single_passthrough(tmp_path, monkeypatch):
    """单张图直接返回原 URL，不拼接。"""
    from app.config import settings
    monkeypatch.setattr(settings, "media_dir", str(tmp_path / "media"))

    url = "https://example.com/single.png"
    result = stitch_grid([url])
    assert result == url  # 单张原样返回


def test_stitch_grid_multiple_creates_file(tmp_path, monkeypatch):
    """多张图拼接为一张，输出文件存在。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    # 创建两张本地测试图
    img1_path = media_dir / "assets" / "char1"
    img2_path = media_dir / "assets" / "char2"
    img1_path.mkdir(parents=True, exist_ok=True)
    img2_path.mkdir(parents=True, exist_ok=True)
    _make_test_image(str(img1_path / "cover.png"), (255, 0, 0))
    _make_test_image(str(img2_path / "cover.png"), (0, 255, 0))

    url1 = "http://localhost:8000/static/media/assets/char1/cover.png"
    url2 = "http://localhost:8000/static/media/assets/char2/cover.png"

    result = stitch_grid([url1, url2])
    assert result is not None
    assert result.startswith("http://localhost:8000/static/media/assets/_grid/")
    # 验证文件存在
    grid_path = media_dir / "assets" / "_grid" / result.split("/")[-1]
    assert grid_path.exists()
    # 验证是有效图片
    img = Image.open(grid_path)
    assert img.size == (1536, 768)  # 2列 × 768 = 1536宽，1行 × 768 = 768高


def test_stitch_grid_empty_returns_none(tmp_path, monkeypatch):
    """空列表返回 None。"""
    result = stitch_grid([])
    assert result is None


def test_stitch_grid_max_four(tmp_path, monkeypatch):
    """超过 4 张只取前 4 张。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    urls = []
    for i in range(6):
        d = media_dir / "assets" / f"char{i}"
        d.mkdir(parents=True, exist_ok=True)
        _make_test_image(str(d / "cover.png"), (i * 40, 0, 0))
        urls.append(f"http://localhost:8000/static/media/assets/char{i}/cover.png")

    result = stitch_grid(urls)
    assert result is not None
    # 验证是 2×2 网格（4 张图 → 2行2列）
    grid_path = media_dir / "assets" / "_grid" / result.split("/")[-1]
    img = Image.open(grid_path)
    assert img.size == (1536, 1536)  # 2×768=1536 宽，2×768=1536 高


def test_load_image_local_file(tmp_path, monkeypatch):
    """本地 media URL 能正确加载为 PIL Image。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    monkeypatch.setattr(settings, "media_dir", str(media_dir))
    d = media_dir / "assets" / "test"
    d.mkdir(parents=True, exist_ok=True)
    _make_test_image(str(d / "cover.png"), (128, 64, 32))

    url = "http://localhost:8000/static/media/assets/test/cover.png"
    img = _load_image(url)
    assert img is not None
    assert img.size == (100, 100)


def test_load_image_nonexistent_returns_none(tmp_path, monkeypatch):
    """不存在的文件返回 None。"""
    from app.config import settings
    monkeypatch.setattr(settings, "media_dir", str(tmp_path / "media"))

    url = "http://localhost:8000/static/media/nonexistent/cover.png"
    assert _load_image(url) is None
