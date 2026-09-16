"""P6 章节续接追加测试：adapt_novel_continuation 断点/范围校验 + 续接落库。

- adapt_novel_continuation：断点顺序校验、章节范围、前文上下文注入、续接落库
- materialize_draft 续接模式：幕 index 续接、资产预填复用、只触发新增幕规划
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.novel import NovelAnalysisStatus
from app.services import script_adaptation_service as sas


# ─── adapt_novel_continuation：校验 ────────────────────────────────

def _fake_novel(raw_text: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="测试小说",
        raw_text=raw_text,
        analysis_status=NovelAnalysisStatus.done,
        analysis_result={"outline": "大纲", "chapters_summary": []},
    )


def _chapters_text(n: int) -> str:
    """生成带章节标记（独占一行）的小说文本，供 split_chapters 真实切分。"""
    lines = []
    for i in range(1, n + 1):
        lines.append(f"第{i}章")
        lines.append(f"第{i}章的内容正文……")
    return "\n".join(lines)


def _fake_project(upto: int, episode_count: int = 3):
    return SimpleNamespace(
        id=uuid.uuid4(),
        processed_upto_chapter=upto,
        episodes=[SimpleNamespace(index=i, title=f"幕{i}", synopsis="概要") for i in range(episode_count)],
        assets=[SimpleNamespace(type=SimpleNamespace(value="character"), name="林浅", description="女主")],
    )


def test_continuation_requires_analyzed_novel(monkeypatch):
    """未分析的小说 → ValueError。"""
    db = MagicMock()
    db.get.return_value = SimpleNamespace(
        id=uuid.uuid4(), title="未分析", raw_text="",
        analysis_status=NovelAnalysisStatus.failed, analysis_result=None,
    )
    with pytest.raises(ValueError, match="尚未分析"):
        sas.adapt_novel_continuation(db, str(uuid.uuid4()), str(uuid.uuid4()),
                                     chapter_start=1, chapter_end=2)


def test_continuation_requires_sequential_checkpoint(monkeypatch):
    """断点校验：chapter_start 必须 = processed_upto_chapter + 1。"""
    db = MagicMock()
    # Novel 已分析
    def _get(model, pk, *a, **kw):
        if model.__name__ == "Novel":
            return _fake_novel(_chapters_text(3))
        if model.__name__ == "Project":
            return _fake_project(upto=2)
        return None
    db.get.side_effect = _get

    with pytest.raises(ValueError, match="按序续接"):
        sas.adapt_novel_continuation(db, str(uuid.uuid4()), str(uuid.uuid4()),
                                     chapter_start=1, chapter_end=2)


def test_continuation_chapter_end_out_of_range(monkeypatch):
    """chapter_end 超出章节数 → ValueError。"""
    db = MagicMock()
    def _get(model, pk, *a, **kw):
        if model.__name__ == "Novel":
            return _fake_novel(_chapters_text(2))
        if model.__name__ == "Project":
            return _fake_project(upto=1)
        return None
    db.get.side_effect = _get

    with pytest.raises(ValueError, match="超出范围"):
        sas.adapt_novel_continuation(db, str(uuid.uuid4()), str(uuid.uuid4()),
                                     chapter_start=2, chapter_end=99)


# ─── adapt_novel_continuation：成功路径 ────────────────────────────

def test_continuation_success(monkeypatch):
    """成功路径：分批摘要 → 续接落库 → 断点更新 → 返回新增幕。"""
    db = MagicMock()
    novel = _fake_novel(_chapters_text(3))
    project = _fake_project(upto=1)
    db.get.side_effect = lambda model, pk, *a, **kw: {
        "Novel": novel, "Project": project,
    }.get(model.__name__)

    # 摘要 mock
    monkeypatch.setattr(
        __import__("app.services.novel_analysis_service", fromlist=["summarize_chapter"]),
        "summarize_chapter",
        lambda ch, provider: {"index": ch["index"], "title": ch["title"], "summary": f"摘要{ch['index']}"},
    )

    # LLM provider mock
    draft = {
        "title": "续接",
        "synopsis": "续接梗概",
        "assets": [{"type": "character", "name": "林浅", "description": "沿用"}],
        "episodes": [{
            "title": "幕3",
            "synopsis": "续接幕",
            "segments": [{
                "shot_type": "中景", "camera": "固定", "description": "画面",
                "characters": ["林浅"], "scene": None, "props": [],
                "emotion": "平静",
            }],
        }],
    }
    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(draft, ensure_ascii=False)}}]}
    monkeypatch.setattr(sas, "_resolve_model", lambda *a, **kw: MagicMock(id="m1"))
    monkeypatch.setattr(
        __import__("app.providers.registry", fromlist=["ProviderRegistry"]).ProviderRegistry,
        "for_model_id", lambda db, mid: provider,
    )
    monkeypatch.setattr(sas.time, "sleep", lambda s: None)

    # 续接落库 mock：追加 1 幕（index=3），返回合并后的 episodes
    merged = list(project.episodes) + [SimpleNamespace(index=3, title="幕3", synopsis="续接幕")]
    materialized = SimpleNamespace(id=project.id, episodes=merged)
    def _fake_materialize(db, draft, *, synopsis, existing_project=None, **kw):
        assert existing_project is project
        return materialized
    monkeypatch.setattr(
        __import__("app.services.llm_script_service", fromlist=["materialize_draft"]),
        "materialize_draft", _fake_materialize,
    )

    result = sas.adapt_novel_continuation(
        db, str(novel.id), str(project.id), chapter_start=2, chapter_end=3,
    )

    assert len(result["added_episodes"]) == 1
    assert result["added_episodes"][0].index == 3
    assert result["chapters"][0]["index"] == 2
    # 断点更新（写到续接后的 project）+ 提交
    assert materialized.processed_upto_chapter == 3
    db.commit.assert_called()
    # prompt 注入前文上下文
    prompt_arg = provider.chat.call_args[0][0][0]["content"]
    assert "已有幕清单" in prompt_arg
    assert "林浅" in prompt_arg
    assert "本批章节内容" in prompt_arg


# ─── adapt_novel：P7.6 每幕时长 + P8 风格/尺寸传参 ─────────────────

def test_adapt_novel_passes_style_ratio_and_per_duration(monkeypatch):
    """分镜模式：adapt_novel 把分镜时长上限与屏幕尺寸传给 materialize_draft；
    未选风格时改编视觉风格兜底写实；prompt 注入分镜时长上限。"""
    from app.providers.registry import ProviderRegistry
    from app.services import llm_script_service

    db = MagicMock()
    novel = _fake_novel(_chapters_text(3))
    db.get.side_effect = lambda model, pk, *a, **kw: {"Novel": novel}.get(model.__name__)

    draft = {"title": "测试", "synopsis": "梗概", "assets": [], "episodes": []}
    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(draft, ensure_ascii=False)}}]}
    monkeypatch.setattr(sas, "_resolve_model", lambda *a, **kw: MagicMock(id="m1"))
    monkeypatch.setattr(ProviderRegistry, "for_model_id", lambda db, mid: provider)
    monkeypatch.setattr(sas.time, "sleep", lambda s: None)

    captured = {}
    def _fake_materialize(db, draft, *, synopsis, aspect_ratio="16:9", style_id=None, art_style_prompt=None, per_duration=None, **kw):
        captured.update(aspect_ratio=aspect_ratio, style_id=style_id, art_style_prompt=art_style_prompt,
                        per_duration=per_duration)
        return SimpleNamespace(id=uuid.uuid4(), title="测试")
    monkeypatch.setattr(llm_script_service, "materialize_draft", _fake_materialize)

    sas.adapt_novel(db, str(novel.id), per_duration=15, style_id=None, aspect_ratio="9:16")

    # 分镜时长上限与屏幕尺寸传给落库
    assert captured["per_duration"] == 15
    assert captured["aspect_ratio"] == "9:16"
    assert captured["style_id"] is None
    # 未选风格 → LLM 改编视觉风格兜底写实；prompt 注入分镜时长上限
    prompt_arg = provider.chat.call_args[0][0][0]["content"]
    assert "写实摄影" in prompt_arg
    assert "分镜时长上限：15s" in prompt_arg


# ─── materialize_draft 续接模式 ────────────────────────────────────

def test_materialize_draft_continuation_reuses_project_and_continues_index(monkeypatch):
    """续接模式：复用已有项目、幕 index 从已有最大 +1、同名资产预填可复用。"""
    from app.services import llm_script_service as lls

    db = MagicMock()
    old_ep = SimpleNamespace(index=0)
    existing_asset = SimpleNamespace(id=uuid.uuid4(), type=SimpleNamespace(value="character"),
                                     name="林浅")
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        episodes=[old_ep],
        assets=[existing_asset],
    )

    # _find_or_create_asset：同名返回已有，新名创建
    new_asset = SimpleNamespace(id=uuid.uuid4())
    def _fake_find(db, *, name, type_, description, project_id_for_new):
        assert project_id_for_new == existing.id  # 新资产挂到已有项目
        if name == "林浅":
            return existing_asset
        return new_asset
    monkeypatch.setattr(lls, "_find_or_create_asset", _fake_find)

    draft = {
        "title": "续接",
        "assets": [
            {"type": "character", "name": "林浅", "description": "沿用"},
            {"type": "character", "name": "新角色", "description": "新增"},
        ],
        "episodes": [
            {"title": "幕2", "segments": [{
                "shot_type": "中景", "camera": "固定", "description": "画面",
                "characters": ["林浅", "新角色"], "scene": None, "props": [],
                "emotion": "平静",
            }]},
        ],
    }
    result = lls.materialize_draft(db, draft, synopsis="续接", existing_project=existing)

    # 复用已有项目对象（不新建 Project）
    assert result is existing

    # 新增幕 index = 已有最大(0) + 1
    added_eps = [a.args[0] for a in db.add.call_args_list if a.args and a.args[0].__class__.__name__ == "Episode"]
    assert len(added_eps) == 1
    assert added_eps[0].index == 1
    assert added_eps[0].project_id == existing.id


# ─── materialize_draft 预写 video_script（P7.6）────────────────────

def test_materialize_draft_prewrites_episode_script(monkeypatch):
    """P7.6：per_duration 非 None 时，每个 Episode 预写 video_script（splits 单段 + 幕级叙事）。"""
    from app.services import llm_script_service as lls

    db = MagicMock()
    created = {}

    def _fake_find(db, *, name, type_, description, project_id_for_new):
        a = SimpleNamespace(id=uuid.uuid4(), name=name, description=description or name)
        created[str(a.id)] = a
        return a
    monkeypatch.setattr(lls, "_find_or_create_asset", _fake_find)

    def _fake_get(model, pk, *a, **kw):
        return created.get(str(pk))
    db.get.side_effect = _fake_get

    draft = {
        "title": "测试剧",
        "assets": [
            {"type": "character", "name": "苏尘", "description": "素白长衫青年"},
            {"type": "scene", "name": "雨巷", "description": "江南雨巷夜景"},
        ],
        "episodes": [
            {"title": "主幕", "synopsis": "概要",
             "narrative": "苏尘撑着油纸伞缓步走来，雨水沿伞沿滴落",
             "first_scene": "雨巷全景，苏尘撑伞身影",
             "last_scene": "苏尘抬眼望向巷口",
             "segments": [
                 {"shot_type": "全景", "camera": "固定", "description": "苏尘步入雨巷",
                  "characters": ["苏尘"], "scene": "雨巷", "props": [],
                  "emotion": "平静", "duration": 4.0},
                 {"shot_type": "近景", "camera": "推", "description": "苏尘抬眼凝望",
                  "characters": ["苏尘"], "scene": "雨巷", "props": [],
                  "emotion": "平静", "duration": 4.0},
             ]},
        ],
    }
    project = lls.materialize_draft(db, draft, synopsis="梗概", per_duration=15)

    eps = [a.args[0] for a in db.add.call_args_list if a.args and a.args[0].__class__.__name__ == "Episode"]
    assert len(eps) == 1
    script = json.loads(eps[0].video_script)
    assert script["per_duration"] == 15
    assert len(script["splits"]) == 1
    assert script["splits"][0]["shot_indexes"] == [1, 2]
    assert script["splits"][0]["narrative"] == "苏尘撑着油纸伞缓步走来，雨水沿伞沿滴落"
    assert script["splits"][0]["first_scene"] == "雨巷全景，苏尘撑伞身影"
    # scene/characters 引用资产描述（人物/场景锚点稳定）
    assert "雨巷：江南雨巷夜景" in script["scene"]
    assert "苏尘：素白长衫青年" in script["characters"]
    assert script["style"].startswith("Art style: ")


def test_materialize_draft_without_per_duration_no_script(monkeypatch):
    """P7.6：未传 per_duration（非改编链路）→ 不预写 video_script，保持现状。"""
    from app.services import llm_script_service as lls

    db = MagicMock()
    monkeypatch.setattr(lls, "_find_or_create_asset",
                        lambda db, *, name, type_, description, project_id_for_new:
                        SimpleNamespace(id=uuid.uuid4(), name=name, description=description or name))

    draft = {
        "title": "测试剧",
        "assets": [],
        "episodes": [
            {"title": "主幕", "segments": [{
                "shot_type": "中景", "camera": "固定", "description": "画面",
                "characters": [], "scene": None, "props": [], "emotion": "平静",
            }]},
        ],
    }
    lls.materialize_draft(db, draft, synopsis="梗概")
    eps = [a.args[0] for a in db.add.call_args_list if a.args and a.args[0].__class__.__name__ == "Episode"]
    assert len(eps) == 1
    assert eps[0].video_script is None


# ─── ai_generate：P7.6 per_duration 透传（Home 一句话生成）─────────

def test_ai_generate_passes_per_duration(monkeypatch):
    """Home 一句话生成项目：per_duration 透传给 materialize_draft（预写 video_script）。"""
    from app.providers.registry import ProviderRegistry
    from app.services import llm_script_service, project_service

    db = MagicMock()
    draft = {"title": "测试", "assets": [], "episodes": []}
    provider = MagicMock()
    provider.chat.return_value = {"choices": [{"message": {"content": json.dumps(draft, ensure_ascii=False)}}]}
    monkeypatch.setattr(ProviderRegistry, "for_model_id", lambda db, mid: provider)
    # ai_generate 内延迟 import llm_script_service，需在源头 patch
    monkeypatch.setattr(llm_script_service, "generate_draft",
                        lambda db, synopsis, **kw: draft)

    captured = {}
    def _fake_materialize(db, draft, *, synopsis, aspect_ratio="16:9", style_id=None, art_style_prompt=None, per_duration=None, **kw):
        captured.update(aspect_ratio=aspect_ratio, style_id=style_id,
                        art_style_prompt=art_style_prompt, per_duration=per_duration)
        return SimpleNamespace(id=uuid.uuid4(), title="测试")
    monkeypatch.setattr(llm_script_service, "materialize_draft", _fake_materialize)

    # 注入真实 AIGenerateBody 校验字段（per_duration 枚举）
    from app.schemas.project import AIGenerateBody
    body = AIGenerateBody(synopsis="一句话梗概", per_duration=15, aspect_ratio="9:16")

    project_service.ai_generate(db, body)

    assert captured["per_duration"] == 15
    assert captured["aspect_ratio"] == "9:16"
    assert captured["style_id"] is None


def test_ai_generate_rejects_invalid_per_duration():
    from pydantic import ValidationError
    from app.schemas.project import AIGenerateBody
    with pytest.raises(ValidationError):
        AIGenerateBody(synopsis="梗概", per_duration=20)

