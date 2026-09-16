"""character_voice_service 单元测试：voice_profile → TTSOpts 解析逻辑。

不依赖 DB/LLM，测试纯函数逻辑。
"""
from app.services import character_voice_service
from app.providers.base import TTSOpts


def test_resolve_voice_id_with_reference_audio():
    """voice_profile 有 reference_audio_url → voice_id 仍为 default（实际用 prompt_wav）。"""
    vp = {"reference_audio_url": "http://example.com/voice.wav"}
    assert character_voice_service._resolve_voice_id(vp) == "default"


def test_resolve_voice_id_without_reference_audio():
    """voice_profile 无 reference_audio_url → voice_id 为 default（用服务端 default 注册声音）。"""
    vp = {}
    assert character_voice_service._resolve_voice_id(vp) == "default"


def test_build_tts_opts_with_reference_audio():
    """有参考音频 → TTSOpts 含 prompt_wav/prompt_text。"""
    vp = {
        "reference_audio_url": "http://example.com/voice.wav",
        "reference_audio_text": "参考音频文本",
        "default_emotion": "平静",
    }
    opts = character_voice_service._build_tts_opts(vp, emotion="愤怒")
    assert opts.prompt_wav == "http://example.com/voice.wav"
    assert opts.prompt_text == "参考音频文本"
    assert opts.emotion == "愤怒"
    assert opts.voice == "default"


def test_build_tts_opts_without_reference_audio():
    """无参考音频 → TTSOpts.prompt_wav 为 None。"""
    vp = {}
    opts = character_voice_service._build_tts_opts(vp, emotion="悲伤")
    assert opts.prompt_wav is None
    assert opts.prompt_text is None
    assert opts.emotion == "悲伤"


def test_build_tts_opts_with_instruct_text():
    """显式 instruct_text 透传。"""
    vp = {}
    opts = character_voice_service._build_tts_opts(
        vp, emotion="愤怒", instruct_text="自定义指令"
    )
    assert opts.instruct_text == "自定义指令"
    assert opts.emotion == "愤怒"


def test_build_tts_opts_empty_profile():
    """voice_profile 为空 dict → 不报错，opts 用默认值。"""
    opts = character_voice_service._build_tts_opts({})
    assert opts.voice == "default"
    assert opts.prompt_wav is None
    assert opts.emotion is None


def test_default_narrator_profile_structure():
    """默认旁白声线结构完整。"""
    from app.services.character_voice_service import _DEFAULT_NARRATOR_PROFILE
    profile = _DEFAULT_NARRATOR_PROFILE
    assert profile["gender"] == "male"
    assert profile["age_group"] == "middle"
    assert "稳重" in profile["timbre_tags"]
    assert profile["default_emotion"] == "平静"
    # 默认旁白无参考音频（用服务端 default）
    assert profile["reference_audio_url"] is None


def test_recommend_voice_profile_extracts_json(monkeypatch):
    """recommend_voice_profile：mock LLM 返回 JSON → 解析为 voice_profile。"""
    from app.services import character_voice_service
    from app.models.asset import AssetType
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.chat.return_value = {
        "choices": [{"message": {"content": '{"gender":"female","age_group":"youth","timbre_tags":["清亮","温柔"],"default_emotion":"温馨","voice_description":"温柔女声"}'}}]
    }
    monkeypatch.setattr(character_voice_service, "_resolve_model", lambda *a, **kw: MagicMock(id="m1"))
    monkeypatch.setattr(character_voice_service.ProviderRegistry, "for_model", lambda m: mock_provider)

    asset = MagicMock()
    asset.name = "林浅"
    asset.description = "温柔女子"
    asset.expanded_description = "温柔善良的女子"
    asset.type = AssetType.character
    db = MagicMock()
    db.get.return_value = asset

    profile = character_voice_service.recommend_voice_profile(db, "asset-1")
    assert profile["gender"] == "female"
    assert profile["default_emotion"] == "温馨"
    # 强制清空 reference_audio_url（由用户指定）
    assert profile["reference_audio_url"] is None
    assert profile["reference_audio_text"] is None


def test_recommend_voice_profile_only_for_character(monkeypatch):
    """非角色资产 → 报错。"""
    from app.services import character_voice_service
    from app.models.asset import AssetType
    from unittest.mock import MagicMock

    asset = MagicMock()
    asset.type = AssetType.scene
    db = MagicMock()
    db.get.return_value = asset

    import pytest
    with pytest.raises(ValueError, match="仅角色资产"):
        character_voice_service.recommend_voice_profile(db, "asset-1")
