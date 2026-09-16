"""去AI味写作工作流 · 单元测试（纯逻辑，不发起 LLM/网络请求）。

覆盖：
- writing_style：档位归一化、注入块组装、审校提示
- humanize_service：拆句稳定性（拼接还原）、JSON 容错、空文本短路
- 提示词契约：script/novel 两套生成提示词已注入 style_block 占位，且 format 可用
"""


# ── writing_style ─────────────────────────────────────────────────

def test_style_mode_normalize():
    from app.services.writing_style import get_style_mode, STYLE_MODES, STYLE_NETWORK
    assert get_style_mode("生活流") == "生活流"
    assert get_style_mode("电影感") == "电影感"
    assert get_style_mode("未知档") == STYLE_NETWORK
    assert get_style_mode(None) == STYLE_NETWORK
    assert get_style_mode("") == STYLE_NETWORK
    assert len(STYLE_MODES) == 3


def test_build_inject_block_contains_rules_and_style():
    from app.services.writing_style import build_inject_block, DEAI_RULES_TEXT
    block = build_inject_block("生活流", include_anchor=True)
    assert DEAI_RULES_TEXT in block
    assert "生活流" in block
    assert "风格锚定样本" in block
    light = build_inject_block("电影感", include_anchor=False)
    assert "风格锚定样本" not in light


def test_humanize_style_hint():
    from app.services.writing_style import humanize_style_hint
    hint = humanize_style_hint("网文爽感")
    assert "网文爽感" in hint


# ── humanize_service 纯函数 ───────────────────────────────────────

def test_split_units_roundtrip():
    from app.services.humanize_service import _split_units
    sample = "【第1集 测试】\n内景·办公室·夜\n他说：“你走吧。” 她愣住了。\n空气安静得可怕。"
    units = _split_units(sample)
    assert "".join(units) == sample
    assert any("她愣住了。" in u for u in units)


def test_extract_json_robust():
    from app.services.humanize_service import _extract_json
    assert _extract_json('```json\n{"flagged": []}\n```') == {"flagged": []}
    assert _extract_json("好的，结果如下：{\"rewrites\": [{\"original\": \"a\", \"rewritten\": \"b\"}]}") == {
        "rewrites": [{"original": "a", "rewritten": "b"}]
    }
    assert _extract_json("没有命中") == {}


def test_detect_empty_text():
    from app.services.humanize_service import detect_ai_flavor
    assert detect_ai_flavor(None, "", None) == []
    assert detect_ai_flavor(None, "   ") == []


def test_rewrite_empty_issues():
    from app.services.humanize_service import rewrite_ai_flavor
    text, pairs = rewrite_ai_flavor(None, "原文本", [], None)
    assert text == "原文本"
    assert pairs == []


# ── 提示词契约：生成提示词已注入 style_block ───────────────────────

def test_script_prompts_have_style_block():
    from app.services.script_writing_service import (
        OUTLINE_PROMPT, EPISODE_SCRIPT_PROMPT, EPISODE_DRAFT_PROMPT,
    )
    assert "{style_block}" in OUTLINE_PROMPT
    assert "{style_block}" in EPISODE_SCRIPT_PROMPT
    assert "AI 味" in EPISODE_DRAFT_PROMPT


def test_novel_prompts_have_style_block():
    from app.services.novel_writing_service import OUTLINE_PROMPT, CHAPTER_PROMPT
    assert "{style_block}" in OUTLINE_PROMPT
    assert "{style_block}" in CHAPTER_PROMPT


def test_style_block_format_compatible():
    """提示词 format 后 style_block 正常渲染（占位符不冲突）。"""
    from app.services import writing_style
    from app.services.novel_writing_service import OUTLINE_PROMPT as N_OUTLINE
    filled = N_OUTLINE.format(
        chapters=5,
        style_block=writing_style.build_inject_block("网文爽感", include_anchor=False),
    )
    assert "去AI味硬规则" in filled
    assert "{style_block}" not in filled