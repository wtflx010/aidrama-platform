"""LLM 剧本服务测试。

测试 _extract_json（JSON 解析容错）和 _link_segment（资产名称→ID 关联）。
_find_or_create_asset 需要 DB，用 MagicMock 模拟。
"""
import json
import uuid
from unittest.mock import MagicMock
from types import SimpleNamespace

from app.services.llm_script_service import _extract_json, _link_segment


# ─── _extract_json：JSON 解析容错 ──────────────────────────────────

def test_extract_json_plain():
    """纯 JSON 直接解析。"""
    text = '{"title": "测试", "segments": []}'
    result = _extract_json(text)
    assert result["title"] == "测试"


def test_extract_json_markdown_fence():
    """带 markdown 代码块也能解析。"""
    text = '```json\n{"title": "测试"}\n```'
    result = _extract_json(text)
    assert result["title"] == "测试"


def test_extract_json_with_prefix_text():
    """前后有杂字也能提取。"""
    text = '好的，这是你的剧本：\n{"title": "雨夜"}\n希望你喜欢。'
    result = _extract_json(text)
    assert result["title"] == "雨夜"


def test_extract_json_raw_newlines_in_string():
    """踩坑：LLM 常在字符串值内放裸换行符（0x0A），strict=False 容错。

    标准 json.loads 会报 "Invalid control character at"，导致 ai-generate 400。
    """
    # 构造带裸换行符的 JSON（模拟 LLM 实际输出）
    text = '```json\n{"title": "测试", "script": "第一行\n第二行\n第三行", "assets": []}\n```'
    result = _extract_json(text)
    assert result["title"] == "测试"
    assert "第一行" in result["script"]
    assert "第二行" in result["script"]


def test_extract_json_nested():
    """嵌套 JSON 结构。"""
    text = json.dumps({
        "title": "测试",
        "assets": [{"type": "character", "name": "林浅"}],
        "episodes": [{"title": "第一幕", "segments": []}],
    })
    result = _extract_json(text)
    assert len(result["assets"]) == 1
    assert result["assets"][0]["name"] == "林浅"
    assert len(result["episodes"]) == 1


# ─── _link_segment：资产名称→ID 关联 ───────────────────────────────

def test_link_segment_associates_character():
    """角色名称正确关联到 character_ids。"""
    char_id = str(uuid.uuid4())
    scene_id = str(uuid.uuid4())
    prop_id = str(uuid.uuid4())
    asset_map = {
        ("character", "林浅"): char_id,
        ("scene", "咖啡馆"): scene_id,
        ("prop", "古剑"): prop_id,
    }
    seg = SimpleNamespace(
        character_ids=[], scene_id=None, prop_ids=[], description=None,
    )
    seg_data = {
        "characters": ["林浅"],
        "scene": "咖啡馆",
        "props": ["古剑"],
    }
    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)

    assert seg.character_ids == [char_id]
    assert seg.scene_id == scene_id
    assert seg.prop_ids == [prop_id]


def test_link_segment_unknown_name_skipped():
    """LLM 返回了未在 assets 中声明的名称 → 跳过不报错。"""
    char_id = str(uuid.uuid4())
    asset_map = {("character", "林浅"): char_id}
    seg = SimpleNamespace(character_ids=[], scene_id=None, prop_ids=[], description=None)
    seg_data = {"characters": ["林浅", "未知角色"], "scene": None, "props": []}

    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)
    assert seg.character_ids == [char_id]  # 只关联已知的


def test_link_segment_empty_arrays():
    """空数组不报错。"""
    seg = SimpleNamespace(character_ids=[], scene_id=None, prop_ids=[], description=None)
    seg_data = {"characters": [], "scene": None, "props": []}

    _link_segment(seg, seg_data=seg_data, asset_map={})
    assert seg.character_ids == []
    assert seg.scene_id is None
    assert seg.prop_ids == []


def test_link_segment_multiple_characters():
    """多角色正确关联。"""
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    asset_map = {
        ("character", "林浅"): id1,
        ("character", "陆远"): id2,
    }
    seg = SimpleNamespace(character_ids=[], scene_id=None, prop_ids=[], description=None)
    seg_data = {"characters": ["林浅", "陆远"], "scene": None, "props": []}

    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)
    assert set(seg.character_ids) == {id1, id2}


# ─── _extract_json：新增容错（尾随逗号 + 无JSON报错）──────────────

def test_extract_json_trailing_comma():
    """踩坑：LLM 常在数组/对象末尾留尾随逗号，标准 json.loads 报错。"""
    text = '{"title": "测试", "assets": [{"name": "林浅",},],}'
    result = _extract_json(text)
    assert result["title"] == "测试"
    assert len(result["assets"]) == 1


def test_extract_json_no_json_raises_clear_error():
    """LLM 返回拒答/纯文本（不含 {}）→ 清晰报错而非晦涩的 json.loads 异常。"""
    import pytest
    with pytest.raises(ValueError, match="不含 JSON 对象"):
        _extract_json("抱歉，我无法为您生成这个内容。")


# ─── _extract_json：字符串值内裸 ASCII 双引号容错（P2 修复）─────────

def test_extract_json_unescaped_quotes_in_string():
    """踩坑：LLM 在中文字符串值内放裸 ASCII 双引号（如 如果"它"真的存在），
    标准 json.loads 报 "Expecting ',' delimiter"。修复后应自动转义并解析成功。

    复现线上报错：剧本模型返回格式异常，无法解析为 JSON。
    """
    text = (
        '```json\n'
        '{\n'
        '  "title": "龙醒赛博纪元",\n'
        '  "synopsis": "考古学家唤醒机械金龙。",\n'
        '  "script": "旁白：都说建国之后不许成精，但如果"它"真的存在呢？",\n'
        '  "assets": []\n'
        '}\n'
        '```'
    )
    result = _extract_json(text)
    assert result["title"] == "龙醒赛博纪元"
    # 内容引号被保留为转义形式，解析后文本包含 "它"
    assert "它" in result["script"]
    assert "如果" in result["script"]


def test_extract_json_multiple_unescaped_quotes():
    """同一段字符串内多处裸 ASCII 双引号都能被修复。"""
    text = (
        '{"title": "测试", '
        '"script": "他说"你好"然后转身走了，留下"再见"两个字。", '
        '"assets": []}'
    )
    result = _extract_json(text)
    assert result["title"] == "测试"
    assert "你好" in result["script"]
    assert "再见" in result["script"]


def test_extract_json_structural_quotes_not_corrupted():
    """回归：合法 JSON 的结构性引号（键、字符串边界）不被误转义。"""
    text = json.dumps({
        "title": "正常标题",
        "assets": [{"type": "character", "name": "林浅", "description": "普通描述"}],
        "episodes": [{"title": "第一幕", "segments": []}],
    }, ensure_ascii=False)
    result = _extract_json(text)
    assert result["title"] == "正常标题"
    assert result["assets"][0]["name"] == "林浅"
    assert len(result["episodes"]) == 1


def test_extract_json_escaped_quotes_preserved():
    """回归：已正确转义的 \\" 不被二次转义。"""
    text = r'{"title": "测试", "script": "他说\"你好\"然后走了。", "assets": []}'
    result = _extract_json(text)
    assert result["script"] == '他说"你好"然后走了。'


# ─── generate_draft：重试机制 ──────────────────────────────────────

def test_generate_draft_retries_on_parse_failure(monkeypatch):
    """JSON 解析失败时自动重试，第三次成功 → 返回结果。"""
    from app.services import llm_script_service

    mock_provider = MagicMock()
    mock_provider.chat.side_effect = [
        {"choices": [{"message": {"content": "这不是JSON"}}]},
        {"choices": [{"message": {"content": "仍然不是JSON"}}]},
        {"choices": [{"message": {"content": '{"title": "成功", "assets": []}'}}]},
    ]

    monkeypatch.setattr(llm_script_service, "_resolve_model", lambda *a, **kw: MagicMock(id="m1"))
    monkeypatch.setattr(llm_script_service.ProviderRegistry, "for_model_id", lambda db, mid: mock_provider)
    monkeypatch.setattr(llm_script_service.time, "sleep", lambda s: None)  # 跳过真实等待

    result = llm_script_service.generate_draft(MagicMock(), "测试梗概", max_retries=3)
    assert result["title"] == "成功"
    assert mock_provider.chat.call_count == 3


def test_generate_draft_raises_after_max_retries(monkeypatch):
    """超过最大重试次数 → 抛 ValueError 含原始内容片段。"""
    from app.services import llm_script_service

    mock_provider = MagicMock()
    mock_provider.chat.return_value = {"choices": [{"message": {"content": "永远不是JSON"}}]}

    monkeypatch.setattr(llm_script_service, "_resolve_model", lambda *a, **kw: MagicMock(id="m1"))
    monkeypatch.setattr(llm_script_service.ProviderRegistry, "for_model_id", lambda db, mid: mock_provider)
    monkeypatch.setattr(llm_script_service.time, "sleep", lambda s: None)

    import pytest
    with pytest.raises(ValueError, match="无法解析为 JSON"):
        llm_script_service.generate_draft(MagicMock(), "测试梗概", max_retries=2)
    assert mock_provider.chat.call_count == 2


# ─── _link_segment：结构化对白 dialogue_lines + emotion 落库（P2）───

def test_link_segment_dialogue_lines_with_emotion_and_character_id():
    """结构化对白：回填 character_id + 落 emotion + 拼接 dialogue 兼容串。"""
    char_id = str(uuid.uuid4())
    asset_map = {("character", "林浅"): char_id}
    seg = SimpleNamespace(
        character_ids=[], scene_id=None, prop_ids=[], description=None,
        dialogue_lines=[], emotion=None, dialogue=None,
    )
    seg_data = {
        "characters": ["林浅"],
        "scene": None,
        "props": [],
        "emotion": "愤怒",
        "dialogue_lines": [
            {"speaker": "林浅", "text": "你怎么能这样！", "emotion": "愤怒"},
            {"speaker": "林浅", "text": "我再也不想见到你。", "emotion": "悲伤"},
        ],
    }
    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)

    assert seg.emotion == "愤怒"
    assert len(seg.dialogue_lines) == 2
    assert seg.dialogue_lines[0]["character_id"] == char_id
    assert seg.dialogue_lines[0]["emotion"] == "愤怒"
    assert seg.dialogue_lines[1]["emotion"] == "悲伤"
    # dialogue 兼容串拼接
    assert seg.dialogue == "林浅：你怎么能这样！\n林浅：我再也不想见到你。"


def test_link_segment_dialogue_lines_multi_character():
    """多角色同镜：每条对白按 speaker 回填对应 character_id。"""
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    asset_map = {
        ("character", "林浅"): id1,
        ("character", "陆远"): id2,
    }
    seg = SimpleNamespace(
        character_ids=[], scene_id=None, prop_ids=[], description=None,
        dialogue_lines=[], emotion=None, dialogue=None,
    )
    seg_data = {
        "characters": ["林浅", "陆远"],
        "scene": None,
        "props": [],
        "emotion": "紧张",
        "dialogue_lines": [
            {"speaker": "林浅", "text": "你来了。", "emotion": "紧张"},
            {"speaker": "陆远", "text": "嗯，我来了。", "emotion": "平静"},
        ],
    }
    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)

    assert seg.dialogue_lines[0]["character_id"] == id1
    assert seg.dialogue_lines[1]["character_id"] == id2


def test_link_segment_dialogue_lines_fallback_to_dialogue_string():
    """无 dialogue_lines（旧格式/纯旁白镜）→ 回退 seg_data['dialogue']，dialogue_lines 为空。"""
    seg = SimpleNamespace(
        character_ids=[], scene_id=None, prop_ids=[], description=None,
        dialogue_lines=[], emotion=None, dialogue=None,
    )
    seg_data = {
        "characters": [],
        "scene": None,
        "props": [],
        "emotion": "平静",
        "dialogue": "旧格式对白",
        # 无 dialogue_lines
    }
    _link_segment(seg, seg_data=seg_data, asset_map={})

    assert seg.dialogue_lines == []
    assert seg.emotion == "平静"
    assert seg.dialogue == "旧格式对白"


def test_link_segment_dialogue_lines_skip_invalid_entries():
    """dialogue_lines 含缺 speaker/text 的脏数据 → 跳过不报错。"""
    char_id = str(uuid.uuid4())
    asset_map = {("character", "林浅"): char_id}
    seg = SimpleNamespace(
        character_ids=[], scene_id=None, prop_ids=[], description=None,
        dialogue_lines=[], emotion=None, dialogue=None,
    )
    seg_data = {
        "characters": ["林浅"],
        "scene": None,
        "props": [],
        "emotion": "平静",
        "dialogue_lines": [
            {"speaker": "林浅", "text": "有效对白", "emotion": "平静"},
            {"speaker": "", "text": "无说话人", "emotion": "平静"},  # 跳过
            {"speaker": "林浅", "text": "", "emotion": "平静"},       # 跳过
            "不是dict",                                                 # 跳过
        ],
    }
    _link_segment(seg, seg_data=seg_data, asset_map=asset_map)

    assert len(seg.dialogue_lines) == 1
    assert seg.dialogue_lines[0]["text"] == "有效对白"
