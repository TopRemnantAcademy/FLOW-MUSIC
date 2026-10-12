"""搜索 / 发现（GAP-014）：标签浏览、多条件筛选、相似推荐、时间窗趋势。

一个 router 承载四件事，因为它们共享同一条边界 —— **公开可见性 + 排序口径**。
拆开会立刻出现「plaza 一份 _public_filters、explore 一份、search 再来一份」的局面，
而上轮已经吃过这个亏（见 plaza.py:_public_filters 注释）。故：

- 公开可见性：**复用** `plaza._public_filters()`（已公开 + 已完成 + 未驳回 + 未软删）。
- 排序：**复用** plaza 的 hot/new/top 口径（HOT_LIKE_WEIGHT 同一常量），不另立一套。
- 不重复造：`/api/plaza`、`/api/songs/explore` 原样保留，本文件只是补上
  「按标签/流派/情绪筛选」与「相似作品」「时间窗趋势」这三件当时确实没有的事。

⚠ 端点刻意**不叫** `/api/search`：路径规划文档里既有 `/api/songs`、`/api/plaza`，
新增前缀会让前端再多记一个命名空间。这里挂在 `/api/search/*` 下是因为
「筛选 + 标签 + 相似 + 趋势」不是任何既有资源的子路径，混进 songs.py 只会让它继续膨胀。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, SongTag, Tag, User
from app.routers.plaza import _public_filters, get_optional_user
from app.routers.songs import _author_names, _interaction_stats, _to_out
from app.schemas import SimilarSongOut, SongOut, TagOut
from app.search import service as tag_service

router = APIRouter(prefix="/api/search", tags=["search"])

# 趋势榜支持的时间窗。7d/30d 是产品定义的两个粒度，不是"随便给的默认值"：
# 更短的窗口（1d）在低流量站上样本不足，排名会变成噪声的抖动。
TREND_WINDOWS = {"7d": 7, "30d": 30}

# 单次筛选允许的标签数上限：多标签是 AND，加得越多结果越少；
# 无上限会拼出一条几十个 EXISTS 的 SQL（且必然返回空），先 400 说清楚。
MAX_FILTER_TAGS = 5


def _parse_tags(raw: list[str] | None, category: Optional[str]) -> list[tuple[str, str]]:
    """把 `?tag=genre:guofeng&tag=mood:sad` 解析成规范 (category, value) 列表。

    格式用 `category:value` 而不是裸 value：`worship` 既可能是 genre 也可能是
    别的分类，裸值会让筛选语义含糊。`category` 参数是**便捷写法**的兜底
    （`?category=genre&tag=guofeng`），两者等价。
    """
    pairs: list[tuple[str, str]] = []
    for item in raw or []:
        text = (item or "").strip()
        if not text:
            continue
        if ":" in text:
            cat, val = text.split(":", 1)
        else:
            cat, val = (category or ""), text
        norm = tag_service.tagrules_normalize(cat, val)
        if norm is None:
            raise HTTPException(
                status_code=400,
                detail=f"未知标签 {text!r}：请使用 /api/search/tags 返回的 category:value",
            )
        if norm not in pairs:
            pairs.append(norm)
        # ⚠ 上限按**请求里给出的条件数**判（而不是去重后的 pairs）：
        #   去重会把 `tag=a&tag=a&tag=a...` 收敛成 1 个，上限就形同不存在 ——
        #   而它要防的正是"用户/脚本拼出几十个条件的 SQL"。
        if len(pairs) > MAX_FILTER_TAGS:
            raise HTTPException(
                status_code=400,
                detail=f"一次最多筛选 {MAX_FILTER_TAGS} 个标签，收到 {len(pairs)} 个",
            )
    return pairs


def _serialize(db: Session, songs: list[Song], user: Optional[User]) -> list[SongOut]:
    """批量序列化公开曲（互动统计 + 作者名一次查完，避免 N+1）。"""
    if not songs:
        return []
    stats = _interaction_stats(db, [s.id for s in songs], user.id if user else None)
    names = _author_names(db, [s.user_id for s in songs])
    out: list[SongOut] = []
    for s in songs:
        st = dict(stats.get(s.id) or {})
        st["author_name"] = names.get(s.user_id)
        out.append(_to_out(s, user, st))
    return out


@router.get("/tags", response_model=list[TagOut])
def list_tags(
    category: Optional[str] = Query(None, pattern="^(genre|mood|language|vocal|form)$"),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
):
    """标签列表（按使用热度降序）—— 标签浏览的**候选源**。

    匿名可读：标签本身不泄露任何用户内容，只是词汇表。
    `usage_count` 让前端能直接展示"国风 (37)"并隐藏空标签，
    但也**不隐藏** usage_count=0 的行（可能是运营刚建的，藏起来就再也找不到）。
    """
    return [
        TagOut(
            id=t.id,
            category=t.category,
            value=t.value,
            label=t.label or t.value,
            usage_count=int(t.usage_count or 0),
        )
        for t in tag_service.list_tags(db, category, limit)
    ]


@router.get("/songs", response_model=list[SongOut])
def search_songs(
    q: Optional[str] = None,
    tag: Optional[list[str]] = Query(None, description="可重复：genre:guofeng&tag=mood:sad"),
    category: Optional[str] = None,
    genre: Optional[str] = None,
    mood: Optional[str] = None,
    sort: str = Query("hot", pattern="^(hot|new|top)$"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """按标签 / 流派 / 情绪 / 关键字筛选公开曲（多条件可组合）。

    排序语义与 `/api/plaza` 完全一致（hot/new/top 同一公式），
    故前端可以在「广场筛选」与「广场列表」之间无缝切换而不改排序期待。
    匿名可读；带 token 时回显 liked_by_me 等状态。
    """
    pairs = _parse_tags(tag, None)
    # 便捷参数与 `tag=` 等价：前端用 chip 点击（tag=）与手写筛选（genre=/mood=）两种入口。
    for cat, val in (("genre", genre), ("mood", mood), ("genre", category)):
        if val:
            norm = tag_service.tagrules_normalize(cat, val)
            if norm is None:
                raise HTTPException(status_code=400, detail=f"未知标签 {val!r}")
            if norm not in pairs:
                pairs.append(norm)

    query = db.query(Song).filter(*_public_filters())
    if q and q.strip():
        # 复用广场的关键字口径（title/lyric/prompt + style），不另立一套匹配规则。
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Song.title).like(like),
                func.lower(Song.lyric).like(like),
                func.lower(Song.prompt).like(like),
                func.lower(Song.style).like(like),
            )
        )
    cond = tag_service.tag_filter(db, pairs)
    if cond is not None:
        query = query.filter(cond)

    query = _apply_sort(db, query, sort)
    songs = query.offset(offset).limit(limit).all()
    return _serialize(db, songs, user)


def _apply_sort(db: Session, query, sort: str):
    """排序口径与 plaza 完全一致（同一份热度公式与同分规则）。

    ⚠ 不能各写一份：`HOT_LIKE_WEIGHT` 改一次就要改两处，漏一处就会出现
    「广场里 A 排在 B 前、筛选页里 B 排在 A 前」这种用户能看见的自相矛盾。
    """
    from app.models import SongLike, SongRating
    from app.routers.plaza import HOT_LIKE_WEIGHT

    if sort == "new":
        return query.order_by(Song.created_at.desc(), Song.id.desc())
    if sort == "top":
        rate_sq = (
            db.query(
                SongRating.song_id,
                func.avg(SongRating.score).label("avg"),
                func.count(SongRating.id).label("n"),
            )
            .group_by(SongRating.song_id)
            .subquery()
        )
        return (
            query.outerjoin(rate_sq, rate_sq.c.song_id == Song.id)
            .order_by(
                func.coalesce(rate_sq.c.avg, 0).desc(),
                func.coalesce(rate_sq.c.n, 0).desc(),
                Song.play_count.desc(),
            )
        )
    like_sq = (
        db.query(SongLike.song_id, func.count(SongLike.id).label("n"))
        .group_by(SongLike.song_id)
        .subquery()
    )
    hot = func.coalesce(like_sq.c.n, 0) * HOT_LIKE_WEIGHT + Song.play_count
    return (
        query.outerjoin(like_sq, like_sq.c.song_id == Song.id)
        .order_by(hot.desc(), Song.created_at.desc())
    )


@router.get("/trending", response_model=list[SongOut])
def trending(
    window: str = Query("7d", pattern="^(7d|30d)$"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """时间窗趋势榜（近 7 / 30 天的**互动增量**）。

    与 hot 的区别：hot 是**累计**热度，老歌靠存量永霸榜首；
    本榜只看窗口内的新增赞/评分/收藏 → 回答"最近什么在起来"。

    ⚠ 明确不声称"播放趋势"：`Song.play_count` 是裸计数器，库里没有播放事件行，
    无法按时间窗统计播放。播放量只参与同分打破（见 similar.trending_window）。
    """
    days = TREND_WINDOWS[window]
    # 复用 same 可见性口径：趋势榜也是对外展示面，不能比广场更宽松。
    songs = tag_service.trending(db, _public_filters(), days=days, limit=limit)
    return _serialize(db, songs, user)


@router.get("/songs/{song_id}/similar", response_model=list[SimilarSongOut])
def similar(
    song_id: int,
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """「这和什么相似」：返回**已审核公开**的相似作品，并给出每一条的推荐理由。

    可见性：源曲必须本身可见于公开流（否则等于提供一个"拿 id 探测私有歌"的接口，
    且私有歌的相似结果里会有它自己的文本信息）。

    ⚠ 返回体带 `reasons`（如 ["genre:guofeng", "mood:sad"]）：相似推荐如果不给理由，
    用户无法判断它是不是瞎猜；给了理由，前端可以展示成标签 chip，产品上也更可信。
    """
    source = db.get(Song, song_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="作品不存在")
    filters = _public_filters()
    # 源曲自身也必须满足公开可见条件（机审开关关闭时按 pending 也放行，与广场一致）
    visible = db.query(Song).filter(Song.id == song_id, *filters).first()
    if visible is None:
        raise HTTPException(status_code=404, detail="作品不存在或未公开")

    scored = tag_service.similar(db, source, filters, limit=limit, with_reason=True)
    songs = [row[0] for row in scored]
    base = _serialize(db, songs, user)
    by_id = {s.id: s for s in base}
    out: list[SimilarSongOut] = []
    for rank_row, song in zip(scored, songs):
        payload = by_id[song.id].model_dump()
        payload["reasons"] = rank_row[2]
        payload["similarity"] = round(float(rank_row[1]), 4)
        out.append(SimilarSongOut(**payload))
    return out


@router.get("/songs/{song_id}/tags", response_model=list[TagOut])
def song_tags(song_id: int, db: Session = Depends(get_db)):
    """单曲标签（供详情页展示 + 前端做「点标签去浏览」的跳转）。"""
    song = db.get(Song, song_id)
    if song is None or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="作品不存在")
    rows = (
        db.query(Tag)
        .join(SongTag, SongTag.tag_id == Tag.id)
        .filter(SongTag.song_id == song_id)
        .order_by(Tag.category.asc(), Tag.id.asc())
        .all()
    )
    return [
        TagOut(
            id=t.id,
            category=t.category,
            value=t.value,
            label=t.label or t.value,
            usage_count=int(t.usage_count or 0),
        )
        for t in rows
    ]
