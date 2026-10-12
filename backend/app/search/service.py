"""标签写入的唯一入口 + 标签/筛选查询。

为什么要有 `sync_song_tags` 这一个入口（而不是让每个路由各写一遍）：
`tags.usage_count` 与 `song_tags` 必须同步（前者是后者的冗余计数，理由见 models.Tag）。
两处写入 = 两处计数漂移 = 标签列表按热度排序时数字对不上实际曲目数，
而且这种漂移**不会报错**，只会在某天有人发现"爵士只有 3 首却排在第一位"。

写路径只有三条（生成建曲 / 后台改分列 / 手动打标），全部落到这里。
"""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import Song, SongTag, Tag
from app.search import tags as tagrules
from app.search.tags import SOURCE_AUTO, SOURCE_ENUM

# 展示名：规范值 → 中文展示名。缺省回退规范值本身（不硬造名字）。
_LABELS: dict[tuple[str, str], str] = {
    ("genre", "guofeng"): "国风",
    ("genre", "folk"): "民谣",
    ("genre", "cpop"): "华语流行",
    ("genre", "kpop"): "K-Pop",
    ("genre", "jpop"): "J-Pop",
    ("genre", "pop"): "流行",
    ("genre", "rock"): "摇滚",
    ("genre", "jazz"): "爵士",
    ("genre", "rnb"): "R&B",
    ("genre", "hiphop"): "说唱",
    ("genre", "citypop"): "City Pop",
    ("genre", "edm"): "电子",
    ("genre", "house"): "浩室",
    ("genre", "lofi"): "Lo-Fi",
    ("genre", "ambient"): "氛围",
    ("genre", "newage"): "新世纪",
    ("genre", "worship"): "赞美敬拜",
    ("genre", "christmas"): "圣诞",
    ("genre", "kids"): "儿歌",
    ("genre", "guqin"): "民乐",
    ("mood", "sad"): "伤感",
    ("mood", "happy"): "欢快",
    ("mood", "calm"): "舒缓",
    ("mood", "epic"): "史诗",
    ("mood", "romantic"): "浪漫",
    ("mood", "dark"): "暗黑",
    ("mood", "energetic"): "活力",
    ("language", "zh"): "中文",
    ("language", "en"): "英文",
    ("language", "ja"): "日文",
    ("language", "ko"): "韩文",
    ("vocal", "male"): "男声",
    ("vocal", "female"): "女声",
    ("form", "instrumental"): "纯音乐",
}


def label_for(category: str, value: str) -> str:
    return _LABELS.get((category, value), value)


def tagrules_normalize(category: str | None, value: str | None) -> tuple[str, str] | None:
    """转发到规则层（router 只依赖 service，避免两处各 import 一次规则表）。"""
    return tagrules.normalize_tag(category, value)


def trending(db: Session, live_filters, *, days: int, limit: int) -> list[Song]:
    """转发到相似/趋势模块（同一份"公开面 + 排序"的实现）。"""
    from app.search.similar import trending_window

    return trending_window(db, live_filters, days=days, limit=limit)


def similar(db: Session, source: Song, live_filters, *, limit: int, with_reason: bool):
    from app.search.similar import similar_songs

    return similar_songs(db, source, live_filters, limit=limit, with_reason=with_reason)


def _get_or_create(db: Session, category: str, value: str) -> Tag:
    """取回或新建规范标签行（并发下由唯一约束兜底，见下方 IntegrityError 处理）。"""
    tag = db.query(Tag).filter(Tag.category == category, Tag.value == value).first()
    if tag is None:
        # label / aliases 是**运营信息**：从规则表回填一次，之后不覆盖
        # （后台可能手工改过展示名，重算标签不该把它改回去）。
        aliases = _aliases_of(category, value)
        tag = Tag(
            category=category,
            value=value,
            label=label_for(category, value),
            aliases=",".join(aliases),
        )
        db.add(tag)
        db.flush()
    return tag


def _aliases_of(category: str, value: str) -> list[str]:
    for cat, table in tagrules.CATEGORY_ORDER:
        if cat == category and value in table:
            return [value, *table[value]]
    return [value]


def _touched_tag_ids(db: Session, song_id: int) -> list[int]:
    """该曲当前的标签 id（重算前的快照）。

    重算 = 先删后建，被删掉的标签在 delete 之后已查不到 —— 而它们的 usage_count
    同样变了（少了一首歌）。故必须在**删之前**记下来，交给 `_refresh_usage`。
    用局部返回值传递，而不是模块级 dict：后者在并发请求下会互相覆盖/泄漏
    （生成是后台任务，同一进程内可并发），且永远清不干净。
    """
    return [row[0] for row in db.query(SongTag.tag_id).filter(SongTag.song_id == song_id)]


def sync_song_tags(db: Session, song: Song, *, replace_auto: bool = True) -> int:
    """把一首歌的标签对齐到「当前规则」；返回写入的关联行数。

    - `replace_auto=True`（默认）：重算前清掉该曲的 auto/enum 行，保留 manual。
      这就是 `source` 列存在的理由 —— 规则演进时能安全重算而不冲掉人的判断。
    - 只对**已完成**的歌建标签：生成中的歌标签是临时的（标题/歌词都会在完成时被
      上游结果覆盖），提前建会在库里留下与最终内容不符的标签，且完成时还得再算一次。

    调用方负责 commit（与建曲同一事务），本函数只 flush。
    """
    if (song.status or "") != "completed":
        return 0
    stale_ids = _touched_tag_ids(db, song.id) if replace_auto else []
    if replace_auto:
        db.query(SongTag).filter(
            SongTag.song_id == song.id,
            SongTag.source.in_((SOURCE_AUTO, SOURCE_ENUM)),
        ).delete(synchronize_session=False)

    wanted = tagrules.tags_for_song(song)
    if not wanted:
        _refresh_usage(db, [song.id], extra_tag_ids=stale_ids)
        return 0
    existing = {
        (row.tag_id, row.source)
        for row in db.query(SongTag.tag_id, SongTag.source).filter(SongTag.song_id == song.id)
    }
    for item in wanted:
        tag = _get_or_create(db, item.category, item.value)
        if (tag.id, item.source) in existing:
            continue
        db.add(SongTag(song_id=song.id, tag_id=tag.id, source=item.source))
    db.flush()
    _refresh_usage(db, [song.id], extra_tag_ids=stale_ids)
    return len(wanted)


def _refresh_usage(
    db: Session, song_ids: list[int], *, extra_tag_ids: list[int] | None = None
) -> None:
    """重算受影响标签的 usage_count。

    只重算**受影响的**标签（不是全表 count）：生成是高频写路径，
    每次全表重算会把写放大成「标签表越大越慢」，而受影响的通常只有几个。

    `extra_tag_ids` = 本次重算中被解除关联的标签（删前快照，见 `_touched_tag_ids`）。
    """
    if not song_ids and not extra_tag_ids:
        return
    tag_ids = {
        row[0]
        for row in db.query(SongTag.tag_id).filter(SongTag.song_id.in_(song_ids or [])).distinct()
    }
    tag_ids |= set(extra_tag_ids or [])
    for tag_id in tag_ids:
        n = db.query(func.count(SongTag.id)).filter(SongTag.tag_id == tag_id).scalar() or 0
        db.query(Tag).filter(Tag.id == tag_id).update(
            {Tag.usage_count: int(n)}, synchronize_session=False
        )


# ─────────────────────────────────────────────────────────────────────────────
# 查询面：标签候选 / 按标签筛选
# ─────────────────────────────────────────────────────────────────────────────


def list_tags(db: Session, category: str | None = None, limit: int = 100) -> list[Tag]:
    """标签列表（按使用热度降序）。

    `usage_count = 0` 的标签也返回：它们可能是刚被运营创建的（还没歌挂上），
    藏起来会让后台/前端都看不到自己刚建的东西。排序上热度优先、同类内按 id。
    """
    q = db.query(Tag)
    if category:
        q = q.filter(Tag.category == category.lower())
    return q.order_by(Tag.usage_count.desc(), Tag.category.asc(), Tag.id.asc()).limit(limit).all()


def tag_filter(db: Session, pairs: list[tuple[str, str]]):
    """(category, value) 列表 → SQL 条件：多个标签按 **AND** 组合。

    「国风 + 伤感」的直觉是"既要国风又要伤感"；用 OR 会越筛越宽
    （加一个标签反而多出一倍结果），与用户预期相反。

    用**半连接**（`Song.id IN (SELECT song_id ...)`）而不是 `Song ↔ SongTag` JOIN：
    JOIN 在"同时命中两个标签"时会产生重复行，必须 DISTINCT，而 DISTINCT 又会与
    `ORDER BY 非选择列`（如 play_count）冲突。半连接每个标签一个独立子查询，
    语义清晰且能吃到 `ix_song_tags_tag_song` 索引。
    """
    conds = []
    for category, value in pairs:
        sub = (
            db.query(SongTag.song_id)
            .join(Tag, Tag.id == SongTag.tag_id)
            .filter(Tag.category == category, Tag.value == value)
        )
        conds.append(Song.id.in_(sub))
    return and_(*conds) if conds else None
