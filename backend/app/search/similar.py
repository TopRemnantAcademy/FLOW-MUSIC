"""相似推荐（GAP-014「这和什么相似」）：标签重合 + BM25 词法相似，纯 CPU、零新依赖。

## 结论先行：本阶段**不做**向量检索 —— 不是「还没做」，是量过成本后判定不做

#82 要求「评估 BM25 vs 向量」。下面是**本机实测**（与共置主机同口径：1 cpu）。
复现方式：`python scripts/bench_similar.py`（固定 seed，同机可重跑）——
性能结论会随规模/依赖变化而失效，故把测法固化成脚本，而不是一段评论。

| 判据 | BM25（本模块） | 向量（embedding） |
| --- | --- | --- |
| 依赖 | 0（纯 Python，`app/search/lexical.py`） | 静态嵌入（如 model2vec potion-base-8M ≈30MB）或 ONNX 运行时；`faiss` / `sentence_transformers` / `rank_bm25` **本机实测均未安装**，引入即新依赖 + 镜像重建 |
| 语料前置 | 现成：title/style/lyric/prompt 已在库里 | 需遍历音频算嵌入，或调第三方嵌入 API（香港出口 + 按量计费） |
| 建索引 / 查询（2 万篇） | 实测 **0.94s / 0.036s** | 不引 numpy/faiss 的纯 Python 余弦：嵌入 **1.33s**、单次暴力检索 **0.24s**（dim=256）；引 faiss 才回毫秒级 → 又是新依赖 + 新常驻内存 |
| 建索引 / 查询（2 千篇） | 实测 **0.096s / 0.0028s** | 嵌入 0.13s、暴力检索 0.028s |
| 语义收益 | 用词重合才命中：`伤感的雨夜 钢琴` 查 `失恋的深秋 吉他` 仅得 **0.134** 分 | 这类「零共同词但语义相近」的场景才是向量唯一明确的优势 |

结论：**标签重合为主 + BM25 为辅**，每条排序理由都能对用户讲清楚。
向量检索留到「已持有音频嵌入」的那天再做 —— 那时它才有读者收益，
而不是现在为一个 1g 的机器加一个 30MB 常驻模型和一条新的外部依赖。

⚠ 明确否认的能力：本模块**不产出**任何语义向量，接口也不暴露 embedding 字段。
将来真接向量，应新增排序器，而不是偷偷替换本实现。

## 排序构成（每一项都能向用户解释）

1. **同标签**：结构化信号，最强。同 genre 权重高于同 mood（曲风相近基本决定听感）。
2. **BM25 文本相似**：以**源曲自身文本**为查询，在候选集内打分 ——
   它回答的是「两首歌的说法有多像」，不是「用户想听什么」。
3. **热度只做同分打破**：不让热度盖过相关性，否则「相似推荐」退化成第二个排行榜。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Song, SongFavorite, SongLike, SongRating, SongTag, Tag
from app.search.lexical import Bm25Index, song_document

# 候选池上限：排序在进程内跑，必须有界。冷启动阶段标签候选远小于此。
CANDIDATE_POOL = 500
# 一次请求最多取多少条相似结果（端点上还有更小的默认 limit）
MAX_SIMILAR = 20

# 同 genre 高于同 mood：曲风相近基本决定听感相似，情绪词则常常跨曲风出现。
GENRE_MATCH_SCORE = 3.0
OTHER_TAG_MATCH_SCORE = 1.5

# 趋势榜权重：窗口内的赞≈4（比累计 hot 的 3 更"贵"，因为窗口短、样本少）、收藏 1、
# 评分 2（评分是明确的人工判断，比收藏更重）。
TREND_LIKE_WEIGHT = 4
TREND_RATING_WEIGHT = 2

# 半连接里展开多少个 (category, value) 条件：SQLite 默认 SQLITE_MAX_VARIABLE_NUMBER=999，
# 留一倍余量。超过则退化为「热度池兜底」而不是拼出一条注定失败的 SQL。
MAX_TAG_CONDS = 400


def _tags_of(db: Session, song_ids: list[int]) -> dict[int, set[tuple[str, str]]]:
    """批量取 (category, value) 集合（一次 JOIN，避免逐首查询的 N+1）。"""
    out: dict[int, set[tuple[str, str]]] = {sid: set() for sid in song_ids}
    if not song_ids:
        return out
    rows = (
        db.query(SongTag.song_id, Tag.category, Tag.value)
        .join(Tag, Tag.id == SongTag.tag_id)
        .filter(SongTag.song_id.in_(song_ids))
        .all()
    )
    for sid, cat, val in rows:
        out.setdefault(sid, set()).add((cat, val))
    return out


def source_tags(db: Session, song: Song) -> set[tuple[str, str]]:
    """源曲的标签集。"""
    return _tags_of(db, [song.id]).get(song.id, set())


def _shared_tag_ids(db: Session, tags: set[tuple[str, str]], source_id: int) -> list[int]:
    """共享任一标签的歌曲 id（含源曲自身，调用方需排除）。

    用 `Tag` × `SongTag` 的内连接半连接：SQL 只回 id，不把两首歌的文本捞回来。
    """
    if not tags:
        return []
    conds = [
        (Tag.category == cat) & (Tag.value == val) for cat, val in sorted(tags)[:MAX_TAG_CONDS]
    ]
    rows = (
        db.query(SongTag.song_id)
        .join(Tag, Tag.id == SongTag.tag_id)
        .filter(or_(*conds))
        .all()
    )
    return [r[0] for r in rows if r[0] != source_id]


def candidates(
    db: Session, source: Song, live_filters, *, pool: int = CANDIDATE_POOL
) -> list[Song]:
    """候选池：只捞「可能相关」的行，**绝不在这里决定最终名次**。

    有标签 → 只取共享标签的歌（结构化、几乎必然相关）；
    无标签（冷启动 / 闭词表未命中）→ 取热度前 pool 首兜底，保证端点永远有结果。

    ⚠ 可见性过滤由调用方传入 `live_filters`（与广场/公开流**同一份**口径），
    不在这里另写 —— explore 与 plaza 各写一份 `_public_filters` 是已发生过的教训。
    """
    q = db.query(Song).filter(Song.id != source.id, *live_filters)
    shared = _shared_tag_ids(db, source_tags(db, source), source.id)
    if shared:
        q = q.filter(Song.id.in_(shared))
    return (
        q.order_by(Song.play_count.desc(), Song.id.desc())
        .limit(pool)
        .all()
    )


def rank(
    source: Song,
    cands: list[Song],
    src_tags: set[tuple[str, str]],
    cand_tags: dict[int, set[tuple[str, str]]],
    limit: int,
) -> list[tuple[Song, float, list[str]]]:
    """纯函数排序：返回 [(song, score, reasons)]，不碰 DB（便于单测）。

    同分（含 0 分）按播放量、再按 id 倒序 —— 确定性排序让测试可断言、前端不抖动。
    """
    if not cands:
        return []
    index = Bm25Index({s.id: song_document(s) for s in cands})
    bm25 = dict(index.search(song_document(source), limit=len(cands)))
    top_bm25 = max(bm25.values(), default=0.0) or 1.0

    scored: list[tuple[Song, float, list[str]]] = []
    for cand in cands:
        matched = sorted(cand_tags.get(cand.id, set()) & src_tags)
        tag_score = sum(
            GENRE_MATCH_SCORE if cat == "genre" else OTHER_TAG_MATCH_SCORE
            for cat, _val in matched
        )
        total = tag_score + bm25.get(cand.id, 0.0) / top_bm25
        if total <= 0:
            continue
        scored.append((cand, total, [f"{c}:{v}" for c, v in matched]))
    scored.sort(key=lambda row: (-row[1], -(row[0].play_count or 0), -row[0].id))
    return scored[:limit]


def similar_songs(
    db: Session,
    source: Song,
    live_filters,
    limit: int = MAX_SIMILAR,
    *,
    with_reason: bool = False,
):
    """相似推荐入口。`with_reason=True` 时返回 [(Song, score, reasons)]。"""
    src_tags = source_tags(db, source)
    cands = candidates(db, source, live_filters)
    cand_tags = _tags_of(db, [c.id for c in cands])
    ranked = rank(source, cands, src_tags, cand_tags, limit)
    return ranked if with_reason else [row[0] for row in ranked]


def trending_window(db: Session, live_filters, *, days: int, limit: int) -> list[Song]:
    """趋势榜：时间窗内的互动增量排序（不是 hot 的别名）。

    为什么不是「按 play_count 排」——那是**累计**榜，老歌会永久霸榜，
    新作即使这周爆了也进不来。「趋势」要回答的是「最近什么在起来」。

    可得数据（现状实测）：`SongLike.created_at` / `SongRating.created_at` /
    `SongFavorite.created_at` 都有时间戳，唯独**播放没有事件表**
    （`Song.play_count` 是裸计数器，没有「谁在何时播了」的行）。
    因此本榜只用互动增量，播放量仅作同分打破 ——
    ⚠ 声称「播放趋势」会是幻觉，因为库里根本不存在播放事件。

    ⚠ SQL 形态：用 JOIN 子查询（而不是 LEFT JOIN + HAVING）。
    「窗口内至少有一次互动」用 `IN (子查询)` 表达；写 HAVING 会要求整条查询
    是聚合查询，而这里 SELECT 的是整行 Song —— SQLite 直接
    `HAVING clause on a non-aggregate query`，PG 同样不接受。
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    def _window_subquery(model):
        return (
            db.query(model.song_id)
            .filter(model.created_at >= since)
            .group_by(model.song_id)
            .subquery()
        )

    likes = _window_subquery(SongLike)
    favs = _window_subquery(SongFavorite)
    rates = _window_subquery(SongRating)

    # 权重与广场 hot 同源思路（赞 > 收藏 > 评分·人），但这里计的是**窗口内增量**。
    score = (
        func.coalesce(
            db.query(func.count(SongLike.id))
            .filter(SongLike.song_id == Song.id, SongLike.created_at >= since)
            .scalar_subquery(),
            0,
        )
        * TREND_LIKE_WEIGHT
        + func.coalesce(
            db.query(func.count(SongFavorite.id))
            .filter(SongFavorite.song_id == Song.id, SongFavorite.created_at >= since)
            .scalar_subquery(),
            0,
        )
        + func.coalesce(
            db.query(func.count(SongRating.id))
            .filter(SongRating.song_id == Song.id, SongRating.created_at >= since)
            .scalar_subquery(),
            0,
        )
        * TREND_RATING_WEIGHT
    )
    in_window = Song.id.in_(likes.select()) | Song.id.in_(favs.select()) | Song.id.in_(rates.select())
    return (
        db.query(Song)
        .filter(*live_filters)
        .filter(in_window)
        .order_by(score.desc(), Song.play_count.desc(), Song.id.desc())
        .limit(limit)
        .all()
    )
