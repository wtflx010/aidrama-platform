"""记忆层 · TF-IDF 语义检索（jieba 分词 + 余弦；无外部 embedding 依赖）。"""

import math
import re


_MEMORY_STOPWORDS = set(
    "的 了 是 在 我 你 他 她 它 们 和 与 或 及 也 都 要 会 能 可以 这个 那个 一个 一些 什么 怎么 "
    "自己 我们 你们 他们 应该 因为 所以 如果 但是 然后 非常 比较 有点 大概 可能 一般 平时 每次 "
    "现在 之前 之后 以后 时候 给 把 让 对 从 到 向 于 用 靠 按 以 就 才 又 再 还 已经 正在 "
    "曾经 一直 经常 很少 不要 不用 别 请 吧 呢 吗 啊 呀 哈 嗯".split()
)


def _memory_tokenize(text: str) -> list[str]:
    """jieba 分词 + 停用词/噪声过滤（返回词元列表）。"""
    import jieba

    jieba.setLogLevel(60)  # 静音首次加载词典的日志
    tokens: list[str] = []
    for w in jieba.lcut((text or "").lower()):
        w = w.strip()
        if not w or w in _MEMORY_STOPWORDS:
            continue
        if re.fullmatch(r"[\d\W_]+", w):  # 纯数字/标点/下划线
            continue
        if re.fullmatch(r"[a-z]+", w) and len(w) < 2:
            continue
        tokens.append(w)
    return tokens


def _tfidf_vector(tokens: list[str], df: dict, n: int) -> dict:
    """TF-IDF 稀疏向量（term → tf*idf）。"""
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    vec: dict[str, float] = {}
    for t, c in tf.items():
        idf = math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0
        vec[t] = c * idf
    return vec


def _cosine_sim(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b.get(k, 0) for k in a)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search_memories(
    db, query: str, scope: str | None = None,
    project_id=None, top_n: int = 5,
) -> list[dict]:
    """记忆语义检索：分词 TF-IDF 向量化后余弦排序，返回 [{id, scope, content, score}]。

    词法近似召回——能匹配同义/近义表达（如「古风」vs「国风」在分词粒度下可召回），
    优于纯 LIKE/关键词枚举；数据量小，运行时计算，不落库向量。
    """
    from app.models.agent import AgentMemory
    from sqlalchemy import select

    q = select(AgentMemory)
    if scope in ("global", "project"):
        q = q.where(AgentMemory.scope == scope)
    if project_id:
        q = q.where(AgentMemory.project_id == project_id)
    # 语义召回需要全量打分，但加一个上限避免记忆表增长后每次对话全表拉进内存
    items = list(db.scalars(q.order_by(AgentMemory.created_at.desc()).limit(2000)).all())
    query = (query or "").strip()
    if not items:
        return []
    if not query:
        return [
            {"id": str(m.id), "scope": m.scope, "content": m.content, "score": 0.0}
            for m in items[: max(top_n, 1)]
        ]
    corpus_tokens = [_memory_tokenize(m.content) for m in items]
    q_tokens = _memory_tokenize(query)
    if not q_tokens:
        return []
    df: dict[str, int] = {}
    for toks in corpus_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = len(items)
    q_vec = _tfidf_vector(q_tokens, df, n)
    scored: list[dict] = []
    for m, toks in zip(items, corpus_tokens):
        score = _cosine_sim(q_vec, _tfidf_vector(toks, df, n))
        if score > 0:
            scored.append({
                "id": str(m.id), "scope": m.scope, "content": m.content,
                "score": round(score, 4),
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(top_n, 1)]
