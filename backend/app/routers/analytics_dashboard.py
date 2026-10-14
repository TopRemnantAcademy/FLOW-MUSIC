"""GAP-020 产品分析看板：把**已有数据**聚合成可查询的产品指标（只读）。

## 为什么是「聚合查询」而不是新表

本批次的迁移槽归 #90，硬约束是 **禁新增迁移 / 禁新增业务表 / 禁改 models.py**。
所以这里**不做事件落库**，而是把已经写在库里的行为事实（`songs` / `users` /
`subscriptions` / `payment_orders` / `song_likes` / `song_favorites` / `song_ratings`
+ `songs.play_count`）聚合成运营看得懂的指标。

⚠️ **诚实的口径边界**（不要在评论/前端里吹成精确 DAU）：
- 真正的 DAU 需要「每次会话一条事件」（本仓库无 `UserEvent` 表）。
  这里用 **`Song` 与互动表的 `created_at` 去重后的日活用户数** 当**代理指标**：
  它只覆盖「产生过作品/互动」的用户，**沉默用户（只登录、只浏览）完全不进统计** →
  代理值 **系统性偏低**，不能当 DAU 上报给业务方。
- 因此响应里每个指标都带 `basis`（口径）与 `caveat`（局限），由前端原样展示。

## 与既有 `/api/admin/stats` 的分工

`/api/admin/stats` = 全量**快照**（曲数/状态分布/累计成本），回答「现在库里有什么」。
本模块 = **时间窗 + 漏斗 + 分布**，回答「近 N 天用户在干什么、在哪一步流失」。
两者刻意不合并：快照无时间维，窗口指标全量累计没意义。

## 隐私

只返回**聚合计数**，任何端点都不返回邮箱/`user_id`/设备标识。
TOP 曲目只给「曲名 + 计数」（曲名本来就是公开广场可见字段），不给作者身份。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing.models import PaymentOrder, Subscription
from app.db import get_db
from app.models import (
    Song,
    SongFavorite,
    SongLike,
    SongRating,
    User,
)
from app.routers.auth import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/analytics", tags=["analytics"])

# 允许的时间窗（天）。刻意白名单而不是任意整数：无上限的窗口会让单次请求扫全表，
# 而「近 100000 天」在运营语义上等于「全部」，没有任何人需要这个粒度。
ALLOWED_WINDOWS = (7, 30)

# 状态口径的唯一真相源（与 songs.py / admin.py 的 status 取值一致）：
#   completed = 成功产出；failed = 失败；pending/processing = 在途。
_TERMINAL_OK = "completed"
_TERMINAL_FAIL = "failed"
_INFLIGHT = ("pending", "processing")


def _utcnow() -> datetime:
    """naive UTC —— SQLite 存的是 naive（`func.now()` = CURRENT_TIMESTAMP），
    Postgres 上是 tz-aware。比较前统一成 naive UTC，否则 PG 上会 tz-aware vs naive 报错。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _since(days: int) -> datetime:
    return _utcnow() - timedelta(days=days)


def _num(v) -> float:
    """Numeric → float。SQLAlchemy 对 SQLite 可能回 Decimal/float/None，统一收口。"""
    return round(float(v or 0), 4)


def _daily_counts(
    db: Session, model, ts_col, days: int, extra=None
) -> dict[str, int]:
    """按天分桶计数（UTC 日期）。返回 {"2026-10-01": 3, ...}。

    ⚠️ 在 **Python 侧**分桶而不是 SQL 的 `date_trunc`/`strftime`：
    两者方言不同（PG 用 date_trunc，SQLite 用 strftime），写成 SQL 就必须按方言分叉，
    而这里的数据量级（运营看板，天级）完全撑得住全量取时间戳回来。
    宁可多取几列，也不要一个在两种库里跑出不同结果的查询。
    """
    q = db.query(ts_col).filter(ts_col.isnot(None), ts_col >= _since(days))
    if extra is not None:
        q = q.filter(extra)
    out: dict[str, int] = {}
    for (ts,) in q.all():
        if ts is None:
            continue
        key = ts.strftime("%Y-%m-%d")
        out[key] = out.get(key, 0) + 1
    return out


def _distinct_users_by_day(db: Session, days: int) -> dict[str, int]:
    """「有行为」的日活用户数（**代理指标**，见模块头部的口径说明）。

    行为 = 生成过作品 OR 点赞/收藏/评分。
    用 Python 侧 `set` 去重而不是 SQL `count(distinct ...) group by date(...)`：
    理由同 `_daily_counts`（避免方言分叉）。用户量级是运营看板级别，内存可控。
    """
    since = _since(days)
    per_day: dict[str, set[int]] = {}
    sources = (
        (Song.user_id, Song.created_at),
        (SongLike.user_id, SongLike.created_at),
        (SongFavorite.user_id, SongFavorite.created_at),
        (SongRating.user_id, SongRating.created_at),
    )
    for uid_col, ts_col in sources:
        rows = (
            db.query(uid_col, ts_col)
            .filter(ts_col.isnot(None), ts_col >= since, uid_col.isnot(None))
            .all()
        )
        for uid, ts in rows:
            if uid is None or ts is None:
                continue
            per_day.setdefault(ts.strftime("%Y-%m-%d"), set()).add(int(uid))
    return {k: len(v) for k, v in per_day.items()}


def _window_total(per_day: dict[str, int]) -> int:
    """窗口内合计（各天求和）。"""
    return int(sum(per_day.values()))


def _peak(per_day: dict[str, int]) -> int:
    return int(max(per_day.values())) if per_day else 0


# ═══════════════════════════════════════════════════════════════════════════════
# 1) 总览：DAU/WAU 代理 + 生成数 + 完成率
# ═══════════════════════════════════════════════════════════════════════════════
def _overview(db: Session, days: int) -> dict:
    since = _since(days)
    active_per_day = _distinct_users_by_day(db, days)

    # 生成数 / 完成率 / 失败数（窗口内新建的曲）
    song_rows = (
        db.query(Song.status, func.count(Song.id))
        .filter(Song.created_at.isnot(None), Song.created_at >= since)
        .group_by(Song.status)
        .all()
    )
    by_status = {str(k or "unknown"): int(v) for k, v in song_rows}
    generated = sum(by_status.values())
    completed = by_status.get(_TERMINAL_OK, 0)
    failed = by_status.get(_TERMINAL_FAIL, 0)
    inflight = sum(by_status.get(s, 0) for s in _INFLIGHT)
    # 完成率分母**排除在途**：把 pending/processing 算进分母会让刚提交一批生成时
    # 完成率瞬间跳水，运营会误判成「批量失败」。这是看板上最容易骗人的一个数。
    settled = completed + failed
    completion_rate = round(completed / settled, 4) if settled else 0.0

    new_users = (
        db.query(func.count(User.id))
        .filter(User.created_at.isnot(None), User.created_at >= since)
        .scalar()
        or 0
    )

    daily_songs = _daily_counts(db, Song, Song.created_at, days)

    return {
        "window_days": days,
        "generated_total": generated,
        "completed": completed,
        "failed": failed,
        "inflight": inflight,
        "completion_rate": completion_rate,
        "by_status": by_status,
        "new_users": int(new_users),
        "active_users": {
            "value": _window_total(active_per_day),
            "peak_daily": _peak(active_per_day),
            "daily": active_per_day,
            "basis": (
                "窗口内「产生过作品或互动（赞/收藏/评分）」的去重用户数；"
                "日粒度按 created_at 的 UTC 日期分桶"
            ),
            "caveat": (
                "**非精确 DAU**：登录/浏览/播放等无落库行为的用户不进统计 → 系统性偏低。"
                "精确 DAU 需 UserEvent 事件表（见 PR 设计草案），本窗口内不落库。"
            ),
        },
        "daily_generated": daily_songs,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2) provider / 引擎分布 + 失败原因 TOP
# ═══════════════════════════════════════════════════════════════════════════════
def _providers(db: Session, days: int) -> dict:
    since = _since(days)

    rows = (
        db.query(Song.engine_name, func.count(Song.id))
        .filter(Song.created_at.isnot(None), Song.created_at >= since)
        .group_by(Song.engine_name)
        .all()
    )
    # engine_name 为空 = 走默认引擎、没落归因名。如实叫它 unlabeled，不要猜成 mureka
    # （猜错会让「换引擎后成本变化」的结论整个建立在幻觉上）。
    by_engine = {(k or "unlabeled"): int(v) for k, v in rows}

    rows = (
        db.query(Song.task_type, func.count(Song.id))
        .filter(Song.created_at.isnot(None), Song.created_at >= since)
        .group_by(Song.task_type)
        .all()
    )
    by_task = {(k or "generate"): int(v) for k, v in rows}

    total = sum(by_engine.values())

    return {
        "window_days": days,
        "total": total,
        "by_engine": by_engine,
        "by_task_type": by_task,
        "basis": "窗口内新建曲目按 engine_name / task_type 分组计数；空值归入 unlabeled",
        "caveat": (
            "engine_name 是**生成时**写入的归因快照，之后改后台默认引擎不会回填老曲 "
            "→ 分布反映的是「当时用了谁」，不是「现在配置是谁」。"
        ),
    }


def _failures(db: Session, days: int, limit: int = 10) -> dict:
    """失败原因 TOP。

    ⚠️ 本仓库**没有** `error`/`failure_reason` 列（models.py 无此字段，禁改）。
    唯一可用的原因字段是 `Song.moderation_note` —— songs.py:_write_back 明确只在
    `status=="failed"` 时把供应商报错摘要写进去。所以：
      · 有 note 的失败 → 按 note 归并成「原因」；
      · 无 note 的失败（例如裸 except 只置 failed）→ 归入「未记录原因」桶。
    这个「未记录原因」桶本身就是**运维待办**：它越大，说明越是静默失败。
    """
    since = _since(days)
    rows = (
        db.query(Song.moderation_note, func.count(Song.id))
        .filter(
            Song.created_at.isnot(None),
            Song.created_at >= since,
            Song.status == _TERMINAL_FAIL,
        )
        .group_by(Song.moderation_note)
        .all()
    )
    reason_map: dict[str, int] = {}
    for note, cnt in rows:
        reason = (note or "").strip()
        if not reason:
            reason = "未记录原因（裸 failed，无 moderation_note）"
        reason_map[reason] = reason_map.get(reason, 0) + int(cnt)

    top = sorted(reason_map.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    total_failed = sum(reason_map.values())
    return {
        "window_days": days,
        "total_failed": total_failed,
        "top": [
            {
                "reason": r,
                "count": c,
                "share": round(c / total_failed, 4) if total_failed else 0.0,
            }
            for r, c in top
        ],
        "basis": "窗口内 status=='failed' 的曲目按 moderation_note 归并计数",
        "caveat": (
            "本仓库无独立 error 列（禁改 models.py），原因取自 moderation_note，"
            "**仅在该字段被写入时可用**；「未记录原因」桶 = 静默失败，越多越该修。"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3) 转化漏斗：注册 → 生成 → 付费
# ═══════════════════════════════════════════════════════════════════════════════
def _funnel(db: Session, days: int) -> dict:
    since = _since(days)

    registered = (
        db.query(func.count(User.id))
        .filter(User.created_at.isnot(None), User.created_at >= since)
        .scalar()
        or 0
    )
    # 「生成过」= 去重用户；分母是注册用户，故必须用 distinct user_id
    generated_users = (
        db.query(func.count(func.distinct(Song.user_id)))
        .filter(Song.created_at.isnot(None), Song.created_at >= since, Song.user_id.isnot(None))
        .scalar()
        or 0
    )
    completed_users = (
        db.query(func.count(func.distinct(Song.user_id)))
        .filter(
            Song.created_at.isnot(None),
            Song.created_at >= since,
            Song.user_id.isnot(None),
            Song.status == _TERMINAL_OK,
        )
        .scalar()
        or 0
    )
    # 付费用户 = 窗口内有 paid 订单（事实）∪ 窗口内创建的 active 订阅（存量）。
    # 两个来源都要：只数订单会漏掉「窗口外下单、窗口内仍在订阅」的存量付费用户，
    # 只数订阅会漏掉「刚付款、订阅还没落」的即时转化。
    # 取并集（同一人既下单又订阅不能重复计）——直接取 id 集合求并，
    # 不先各查一次 count(distinct)：查了也必须求并，那两次 count 就是纯浪费。
    order_uids = {
        int(r[0])
        for r in db.query(PaymentOrder.user_id)
        .filter(
            PaymentOrder.created_at.isnot(None),
            PaymentOrder.created_at >= since,
            PaymentOrder.status == "paid",
        )
        .all()
        if r[0] is not None
    }
    sub_uids = {
        int(r[0])
        for r in db.query(Subscription.user_id)
        .filter(
            Subscription.created_at.isnot(None),
            Subscription.created_at >= since,
            Subscription.status == "active",
        )
        .all()
        if r[0] is not None
    }
    paid_users = len(order_uids | sub_uids)

    revenue = _num(
        db.query(func.coalesce(func.sum(PaymentOrder.amount_cny), 0))
        .filter(
            PaymentOrder.created_at.isnot(None),
            PaymentOrder.created_at >= since,
            PaymentOrder.status == "paid",
        )
        .scalar()
    )

    def _rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    steps = [
        {"key": "registered", "label": "注册", "users": int(registered)},
        {"key": "generated", "label": "生成过作品", "users": int(generated_users)},
        {"key": "completed", "label": "有成功作品", "users": int(completed_users)},
        {"key": "paid", "label": "付费（下单或订阅）", "users": int(paid_users)},
    ]
    for i, s in enumerate(steps):
        s["from_prev"] = _rate(s["users"], steps[i - 1]["users"]) if i else 1.0
        s["from_top"] = _rate(s["users"], steps[0]["users"])

    return {
        "window_days": days,
        "steps": steps,
        "paid_users": int(paid_users),
        "paid_revenue_cny": revenue,
        "arppu_cny": _num(revenue / paid_users) if paid_users else 0.0,
        "basis": "窗口内：users.created_at → 有 songs 的用户 → 有 completed 曲的用户 → paid 订单∪active 订阅",
        "caveat": (
            "「注册」用的是 users.created_at 落在窗口内 —— 老用户在本窗口内的付费不会"
            "出现在注册步的分子里，故 from_top 不是「本期注册者转化率」，"
            "而是「本窗口各环节人数之比」。要算 cohort 留存需事件表（本窗口内不做）。"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4) 消费侧：播放 / 互动 / TOP 曲目
# ═══════════════════════════════════════════════════════════════════════════════
def _engagement(db: Session, days: int, top_limit: int = 10) -> dict:
    since = _since(days)

    # ⚠️ play_count 是**累计计数器**，不是带时间的事件 → 无法按窗口切分。
    # 这里故意返回 all_time 字段名，避免前端把它当成「近 7 天播放量」（那是幻觉数字）。
    play_total_all_time = int(db.query(func.coalesce(func.sum(Song.play_count), 0)).scalar() or 0)

    likes = (
        db.query(func.count(SongLike.id))
        .filter(SongLike.created_at.isnot(None), SongLike.created_at >= since)
        .scalar()
        or 0
    )
    favorites = (
        db.query(func.count(SongFavorite.id))
        .filter(SongFavorite.created_at.isnot(None), SongFavorite.created_at >= since)
        .scalar()
        or 0
    )
    ratings = (
        db.query(func.count(SongRating.id))
        .filter(SongRating.created_at.isnot(None), SongRating.created_at >= since)
        .scalar()
        or 0
    )

    top_rows = (
        db.query(Song.id, Song.title, Song.play_count, Song.is_public)
        .filter(Song.deleted_at.is_(None), Song.play_count > 0)
        .order_by(Song.play_count.desc(), Song.id.asc())
        .limit(top_limit)
        .all()
    )
    top_played = [
        {
            "song_id": int(sid),
            "title": (title or "未命名")[:80],
            "play_count": int(pc or 0),
            "is_public": bool(pub),
        }
        for sid, title, pc, pub in top_rows
    ]

    return {
        "window_days": days,
        "play_count_all_time": play_total_all_time,
        "likes_in_window": int(likes),
        "favorites_in_window": int(favorites),
        "ratings_in_window": int(ratings),
        "top_played": top_played,
        "basis": "互动数按窗口内 created_at 计数；播放数为 songs.play_count 全量累计（无时间维）",
        "caveat": (
            "**播放数不可按窗口切分**：play_count 是累加计数器（POST /api/songs/{id}/play +1），"
            "不是事件流，故无「近 7 天播放」这种数。要窗口播放量需事件表。"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════════════════════════════
def _resolve_days(days: int) -> int:
    """窗口白名单归一。非法值落回 7（不 422）——看板是只读观测面，
    给个可用默认比因为拼错参数整页空白更合适。"""
    return days if days in ALLOWED_WINDOWS else ALLOWED_WINDOWS[0]


@router.get("/overview")
def analytics_overview(
    days: int = Query(7, description="时间窗（天），仅支持 7 / 30"),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """总览：生成数 / 完成率 / 活跃用户代理 / 新注册 / 日趋势。"""
    return _overview(db, _resolve_days(days))


@router.get("/providers")
def analytics_providers(
    days: int = Query(7),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """provider（引擎）与任务类型分布。"""
    return _providers(db, _resolve_days(days))


@router.get("/failures")
def analytics_failures(
    days: int = Query(7),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """失败原因 TOP（源自 moderation_note，口径见响应内的 caveat）。"""
    return _failures(db, _resolve_days(days), limit=limit)


@router.get("/funnel")
def analytics_funnel(
    days: int = Query(30),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """注册 → 生成 → 成功 → 付费 转化漏斗。"""
    return _funnel(db, _resolve_days(days))


@router.get("/engagement")
def analytics_engagement(
    days: int = Query(7),
    top_limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """消费侧：互动计数 + 播放 TOP（播放为全量累计，见 caveat）。"""
    return _engagement(db, _resolve_days(days), top_limit=top_limit)


@router.get("/summary")
def analytics_summary(
    days: int = Query(7),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """一次拿全看板所需指标（前端单请求渲染，避免 5 个并发请求各扫一遍库）。

    语义上等价于依次调用上面 5 个端点，返回同一批结构（前端也复用同类型）。
    """
    d = _resolve_days(days)
    return {
        "window_days": d,
        "generated_at": _utcnow().isoformat() + "Z",
        "overview": _overview(db, d),
        "providers": _providers(db, d),
        "failures": _failures(db, d),
        "funnel": _funnel(db, d),
        "engagement": _engagement(db, d),
        "disclaimer": (
            "所有指标均为**代理口径**（无 UserEvent 事件表）：活跃用户仅统计产生过作品/互动的用户，"
            "播放量为全量累计不可切窗。每个指标块内的 basis/caveat 为权威口径说明。"
        ),
    }
