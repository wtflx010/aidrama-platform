"""上传后续章节 API 测试：append_novel_chapters 合并逻辑。

- 成功：新章节合并进 raw_text，chapters_count/word_count 更新，返回带断点标记的章节列表
- 无新章节标记 → 400（拒绝合并，raw_text 不变）
- 空内容 → 400
- 小说不存在 → 404
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1 import novels as api
from app.models.novel import Novel
from app.schemas.novel import AppendChaptersBody


def _chapters_text(n: int, offset: int = 0) -> str:
    """生成带章节标记（独占一行）的文本；offset 用于生成"后续"章节号。"""
    lines = []
    for i in range(1, n + 1):
        lines.append(f"第{i + offset}章")
        lines.append(f"第{i + offset}章的内容正文……")
    return "\n".join(lines)


def _fake_novel(raw_text: str, project_id=None):
    n = SimpleNamespace(
        id=uuid.uuid4(),
        title="测试小说",
        raw_text=raw_text,
        project_id=project_id,
        chapters_count=0,
        word_count=0,
    )
    return n


def test_append_chapters_success():
    """成功合并：追加 2 章 → 总章数 5、word_count 更新、返回新章节列表。"""
    db = MagicMock()
    novel = _fake_novel(_chapters_text(3))
    db.get.return_value = novel

    result = api.append_novel_chapters(
        novel.id, AppendChaptersBody(text=_chapters_text(2, offset=3)), db
    )

    # 合并落库
    assert novel.chapters_count == 5
    assert novel.word_count == len(novel.raw_text)
    assert "第4章" in novel.raw_text and "第5章" in novel.raw_text
    db.commit.assert_called_once()
    # 返回章节列表（断点 0 → 全部未处理）
    assert len(result) == 5
    assert result[0]["index"] == 1
    assert all(not c["processed"] for c in result)


def test_append_chapters_keeps_checkpoint_mark():
    """合并后断点保留：默认项目 processed_upto_chapter=2 → 前 2 章标记已追加。"""
    db = MagicMock()
    novel = _fake_novel(_chapters_text(3), project_id=uuid.uuid4())
    project = SimpleNamespace(id=novel.project_id, processed_upto_chapter=2)

    def _get(model, pk, *a, **kw):
        if model is Novel:
            return novel
        if model.__name__ == "Project":
            return project
        return None
    db.get.side_effect = _get

    result = api.append_novel_chapters(
        novel.id, AppendChaptersBody(text=_chapters_text(2, offset=3)), db
    )

    assert [c["processed"] for c in result] == [True, True, False, False, False]


def test_append_chapters_rejects_text_without_markers():
    """无新章节标记 → 400，raw_text / chapters_count 不变。"""
    db = MagicMock()
    novel = _fake_novel(_chapters_text(3))
    db.get.return_value = novel
    raw_before = novel.raw_text

    with pytest.raises(HTTPException) as ei:
        api.append_novel_chapters(novel.id, AppendChaptersBody(text="没有标记的纯文本……"), db)
    assert ei.value.status_code == 400
    assert "章节标记" in ei.value.detail
    assert novel.raw_text == raw_before
    assert novel.chapters_count == 0  # 未提交更新（默认 0）
    db.commit.assert_not_called()


def test_append_chapters_empty_text():
    """空内容 → 400。"""
    db = MagicMock()
    db.get.return_value = _fake_novel(_chapters_text(3))
    with pytest.raises(HTTPException) as ei:
        api.append_novel_chapters(uuid.uuid4(), AppendChaptersBody(text="   "), db)
    assert ei.value.status_code == 400
    assert "不能为空" in ei.value.detail


def test_append_chapters_novel_not_found():
    """小说不存在 → 404。"""
    db = MagicMock()
    db.get.return_value = None
    with pytest.raises(HTTPException) as ei:
        api.append_novel_chapters(uuid.uuid4(), AppendChaptersBody(text="第1章 内容"), db)
    assert ei.value.status_code == 404
