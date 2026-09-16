"""media_url_to_data_uri 单元测试。

覆盖 4 个分支：
1. 本地 media URL → 转 base64 data URI
2. 公网 URL → 原样返回
3. 空值 → 返回 None
4. 本地文件不存在 → 原样返回 URL
5. 不同扩展名 MIME 映射
"""
import base64

from app.utils.media import media_url_to_data_uri


def test_none_returns_none():
    """空值返回 None。"""
    assert media_url_to_data_uri(None) is None


def test_empty_string_returns_empty():
    """空字符串原样返回。"""
    assert media_url_to_data_uri("") == ""


def test_public_url_passthrough():
    """公网 URL 原样返回，不做转换。"""
    url = "https://platform-outputs.agnes-ai.space/images/t2i/abc.png"
    assert media_url_to_data_uri(url) == url


def test_local_url_converted_to_data_uri(tmp_path, monkeypatch):
    """本地 media URL 转 base64 data URI。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    asset_dir = media_dir / "assets" / "char-1"
    asset_dir.mkdir(parents=True)
    img_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    (asset_dir / "cover.png").write_bytes(img_content)

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    local_url = "http://localhost:8000/static/media/assets/char-1/cover.png"
    result = media_url_to_data_uri(local_url)

    assert result.startswith("data:image/png;base64,")
    # 验证 base64 内容正确
    b64_part = result.split(",", 1)[1]
    assert base64.b64decode(b64_part) == img_content


def test_local_file_not_found_returns_original_url(tmp_path, monkeypatch):
    """本地文件不存在时原样返回 URL（交由上游报错，便于定位）。"""
    from app.config import settings
    monkeypatch.setattr(settings, "media_dir", str(tmp_path / "media"))

    local_url = "http://localhost:8000/static/media/assets/nonexistent/cover.png"
    result = media_url_to_data_uri(local_url)

    assert result == local_url  # 原样返回


def test_jpeg_mime_mapping(tmp_path, monkeypatch):
    """JPEG 文件映射为 image/jpeg MIME。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    (media_dir / "img").mkdir(parents=True)
    (media_dir / "img" / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    url = "http://localhost:8000/static/media/img/photo.jpg"
    result = media_url_to_data_uri(url)

    assert result.startswith("data:image/jpeg;base64,")


def test_webp_mime_mapping(tmp_path, monkeypatch):
    """WEBP 文件映射为 image/webp MIME。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    (media_dir / "img").mkdir(parents=True)
    (media_dir / "img" / "art.webp").write_bytes(b"RIFF" + b"\x00" * 20)

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    url = "http://localhost:8000/static/media/img/art.webp"
    result = media_url_to_data_uri(url)

    assert result.startswith("data:image/webp;base64,")


def test_unknown_extension_defaults_to_png(tmp_path, monkeypatch):
    """未知扩展名默认用 image/png MIME。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    (media_dir / "img").mkdir(parents=True)
    (media_dir / "img" / "file.xyz").write_bytes(b"\x00" * 10)

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    url = "http://localhost:8000/static/media/img/file.xyz"
    result = media_url_to_data_uri(url)

    assert result.startswith("data:image/png;base64,")
