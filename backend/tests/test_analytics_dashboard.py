"""GAP-020 产品分析看板：聚合口径测试。

本文件**断言具体数值**（不是「200 即通过」）。理由：看板是运营决策的输入端，
一个恒返回 0 的聚合端点同样能通过 200 断言，却会让整块业务建立在幻觉上。

钉住的契约：
1. 鉴权：未授权 401 / 未配置 503（沿用既有 require_admin，不新写鉴权路径）。
2. 完成率**分母排除在途**：pending/processing 不计入分母 —— 否则刚批量提交时
   完成率跳水，运营会误判成批量失败。
3. 窗口生效：窗口外的数据不参与统计。
4. 活跃用户是**去重**的（同一用户多首曲只算一个）。
5. 失败原因来自 moderation_note，无 note 的失败归入「未记录原因」桶。
6. provider 分布空值归 unlabeled（不猜成 mureka）。
7. 漏斗付费用户 = paid 订单 ∪ active 订阅 **并集**（同一人不重复计）。
8. 播放数是累计值，字段名不暗示窗口（play_count_all_time）。
"""

from datetime import datetime, timedelta, timezone

from app.billing.models import PaymentOrder, Subscription
from app.config import settings
from app.db import SessionLocal
from app.models import Song, SongFavorite, SongLike, SongRating, User

ADMIN = {"X-Admin-Token": "secret"}


def _utc(**kw) -> datetime:
    """naive UTC（与 SQLite CURRENT_TIMESTAMP 同口径，见 routers/analytics_dashboard.py）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**kw)


def _mk_user(db, email: str, *, created_days_ago: int = 0) -> User:
    u = User(
        email=email,
        hashed_password="x",
        created_at=_utc(days=created_days_ago),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _mk_song(
    db,
    user: User,
    *,
    status: str = "completed",
    engine_name: str | None = "mureka",
    task_type: str = "generate",
    note: str = "",
    play_count: int = 0,
    created_days_ago: int = 0,
    title: str | None = None,
) -> Song:
    s = Song(
        user_id=user.id,
        title=title or f"song-{status}-{engine_name}",
        status=status,
        engine_name=engine_name,
        task_type=task_type,
        moderation_note=note,
        play_count=play_count,
        created_at=_utc(days=created_days_ago),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── 鉴权 ────────────────────────────────────────────────────────────────────
def test_analytics_requires_admin(client, monkeypatch):
    """未带令牌 401；admin_token 未配置 503（沿用既有 require_admin）。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    for path in ("overview", "providers", "failures", "funnel", "engagement", "summary"):
        r = client.get(f"/api/admin/analytics/{path}")
        assert r.status_code == 401, f"{path} 应 401，实际 {r.status_code}"

    monkeypatch.setattr(settings, "admin_token", "")
    r = client.get("/api/admin/analytics/overview")
    assert r.status_code == 503


def test_analytics_viewer_token_can_read(client, monkeypatch):
    """viewer（只读管理员）可读 —— 本模块全 GET，不应被 RBAC 中间件拦。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer")
    r = client.get("/api/admin/analytics/overview", headers={"X-Admin-Token": "viewer"})
    assert r.status_code == 200


# ── 总览：完成率 / 窗口 / 去重 ───────────────────────────────────────────────
def test_overview_completion_rate_excludes_inflight(client, monkeypatch):
    """完成率 = completed / (completed + failed)，pending/processing 不进分母。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "ov@test.com")
        _mk_song(db, u, status="completed")
        _mk_song(db, u, status="completed")
        _mk_song(db, u, status="failed", note="供应商 502")
        _mk_song(db, u, status="processing")  # 在途：不计入分母
        _mk_song(db, u, status="pending")  # 在途：不计入分母
    finally:
        db.close()

    body = client.get("/api/admin/analytics/overview?days=7", headers=ADMIN).json()
    assert body["generated_total"] == 5
    assert body["completed"] == 2
    assert body["failed"] == 1
    assert body["inflight"] == 2
    # 2/(2+1) = 0.6667；若把在途算进分母会是 2/5 = 0.4
    assert body["completion_rate"] == 0.6667
    assert body["by_status"] == {"completed": 2, "failed": 1, "processing": 1, "pending": 1}


def test_overview_window_filters_old_rows(client, monkeypatch):
    """近 7 天窗口必须把 10 天前的数据排除。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "win@test.com", created_days_ago=10)
        _mk_song(db, u, status="completed", created_days_ago=1)
        _mk_song(db, u, status="completed", created_days_ago=10)  # 窗口外
    finally:
        db.close()

    w7 = client.get("/api/admin/analytics/overview?days=7", headers=ADMIN).json()
    assert w7["generated_total"] == 1
    assert w7["new_users"] == 0  # 用户注册于 10 天前，不在 7 天窗内

    w30 = client.get("/api/admin/analytics/overview?days=30", headers=ADMIN).json()
    assert w30["generated_total"] == 2
    assert w30["new_users"] == 1


def test_overview_active_users_are_distinct_per_day(client, monkeypatch):
    """活跃用户按 (日, user) 去重：同一用户当天 3 首曲只算 1 个活跃用户。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u1 = _mk_user(db, "a1@test.com")
        u2 = _mk_user(db, "a2@test.com")
        _mk_song(db, u1, status="completed")
        _mk_song(db, u1, status="failed", note="x")
        _mk_song(db, u1, status="completed")
        _mk_song(db, u2, status="completed")
    finally:
        db.close()

    body = client.get("/api/admin/analytics/overview?days=7", headers=ADMIN).json()
    assert body["active_users"]["value"] == 2  # 不是 4
    assert body["active_users"]["peak_daily"] == 2
    assert "非精确 DAU" in body["active_users"]["caveat"]


def test_overview_invalid_window_falls_back_to_7(client, monkeypatch):
    """非法窗口归一为 7（不 422）——观测面给可用默认比整页空白更合适。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    body = client.get("/api/admin/analytics/overview?days=9999", headers=ADMIN).json()
    assert body["window_days"] == 7


# ── provider / 失败原因 ─────────────────────────────────────────────────────
def test_providers_distribution_and_unlabeled(client, monkeypatch):
    """按引擎/任务类型分布；engine_name 为空归 unlabeled（不猜具体引擎）。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "pv@test.com")
        _mk_song(db, u, engine_name="mureka", task_type="generate")
        _mk_song(db, u, engine_name="mureka", task_type="extend")
        _mk_song(db, u, engine_name="ace_step_private", task_type="generate")
        _mk_song(db, u, engine_name=None, task_type="generate")
    finally:
        db.close()

    body = client.get("/api/admin/analytics/providers?days=7", headers=ADMIN).json()
    assert body["total"] == 4
    assert body["by_engine"] == {"mureka": 2, "ace_step_private": 1, "unlabeled": 1}
    assert body["by_task_type"] == {"generate": 3, "extend": 1}


def test_failures_top_from_moderation_note(client, monkeypatch):
    """失败原因按 moderation_note 归并；无 note 的失败进「未记录原因」桶并计入占比。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "fl@test.com")
        _mk_song(db, u, status="failed", note="供应商 502")
        _mk_song(db, u, status="failed", note="供应商 502")
        _mk_song(db, u, status="failed", note="轮询超时")
        _mk_song(db, u, status="failed", note="")  # 静默失败
        _mk_song(db, u, status="completed", note="不该被算")  # 非 failed 不计
    finally:
        db.close()

    body = client.get("/api/admin/analytics/failures?days=7", headers=ADMIN).json()
    assert body["total_failed"] == 4
    top = {r["reason"]: r for r in body["top"]}
    assert top["供应商 502"]["count"] == 2
    assert top["供应商 502"]["share"] == 0.5
    assert top["轮询超时"]["count"] == 1
    assert any("未记录原因" in r for r in top)
    # 排序：次数降序
    assert [r["count"] for r in body["top"]] == sorted(
        [r["count"] for r in body["top"]], reverse=True
    )


# ── 漏斗 ────────────────────────────────────────────────────────────────────
def test_funnel_paid_users_union_of_order_and_subscription(client, monkeypatch):
    """付费用户 = paid 订单 ∪ active 订阅 的**并集**：同一人不重复计。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u1 = _mk_user(db, "f1@test.com")
        u2 = _mk_user(db, "f2@test.com")
        u3 = _mk_user(db, "f3@test.com")  # 只注册，无作品
        _mk_song(db, u1, status="completed")
        _mk_song(db, u2, status="failed", note="x")

        # u1：既下单又订阅 → 并集里只算 1 人
        db.add(
            PaymentOrder(
                user_id=u1.id, provider="manual", tier_key="standard", amount_cny=100, status="paid"
            )
        )
        db.add(
            Subscription(
                user_id=u1.id, tier_key="standard", provider="manual", status="active"
            )
        )
        # u2：仅订阅
        db.add(Subscription(user_id=u2.id, tier_key="lite", provider="manual", status="active"))
        # 未支付订单 & 非 active 订阅：都不算付费用户
        db.add(
            PaymentOrder(
                user_id=u3.id, provider="manual", tier_key="lite", amount_cny=50, status="pending"
            )
        )
        db.commit()
    finally:
        db.close()

    body = client.get("/api/admin/analytics/funnel?days=30", headers=ADMIN).json()
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["registered"]["users"] == 3
    assert steps["generated"]["users"] == 2
    assert steps["completed"]["users"] == 1
    assert steps["paid"]["users"] == 2  # u1 ∪ u2，u3 的 pending 订单不算
    assert body["paid_users"] == 2
    assert body["paid_revenue_cny"] == 100.0
    assert body["arppu_cny"] == 50.0  # 100 / 2
    assert steps["generated"]["from_top"] == 0.6667  # 2/3


# ── 消费侧 ──────────────────────────────────────────────────────────────────
def test_engagement_counts_and_play_is_all_time(client, monkeypatch):
    """互动按窗口计数；播放力 全量累计且字段名不暗示窗口。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "en@test.com")
        u2 = _mk_user(db, "en2@test.com")
        u3 = _mk_user(db, "en3@test.com")
        hot = _mk_song(db, u, play_count=90, created_days_ago=20)  # 窗口外创建，但累计播放要算
        hot_id = hot.id
        _mk_song(db, u, play_count=10, title="cold")
        # ⚠️ song_likes 有 (song_id, user_id) 唯一约束 → 一人一赞，需用不同用户凑出 2 赞
        db.add(SongLike(song_id=hot_id, user_id=u.id))
        db.add(SongLike(song_id=hot_id, user_id=u2.id))
        db.add(SongFavorite(song_id=hot_id, user_id=u.id))
        db.add(SongRating(song_id=hot_id, user_id=u.id, score=5))
        # 窗口外互动：不计入窗口计数（换人，避免撞唯一约束）
        _old_like = SongLike(song_id=hot_id, user_id=u3.id)
        db.add(_old_like)
        db.commit()
        db.execute(
            SongLike.__table__.update()
            .where(SongLike.__table__.c.id == _old_like.id)
            .values(created_at=_utc(days=40))
        )
        db.commit()
    finally:
        db.close()

    body = client.get("/api/admin/analytics/engagement?days=7", headers=ADMIN).json()
    assert body["likes_in_window"] == 2  # 40 天前那条被排除
    assert body["favorites_in_window"] == 1
    assert body["ratings_in_window"] == 1
    assert body["play_count_all_time"] == 100  # 全量累计（90+10），非窗口
    assert body["top_played"][0]["play_count"] == 90
    assert body["top_played"][0]["song_id"] == hot_id
    assert "不可按窗口切分" in body["caveat"]


def test_engagement_excludes_soft_deleted_from_top(client, monkeypatch):
    """回收站里的歌不进播放 TOP（deleted_at 非空 = 用户已删，不该出现在榜单）。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "dl@test.com")
        gone = _mk_song(db, u, play_count=999)
        gone.deleted_at = _utc(days=1)
        db.commit()
        gone_id = gone.id
        alive = _mk_song(db, u, play_count=5)
        alive_id = alive.id
    finally:
        db.close()

    body = client.get("/api/admin/analytics/engagement?days=7", headers=ADMIN).json()
    ids = [r["song_id"] for r in body["top_played"]]
    assert alive_id in ids
    assert gone_id not in ids


# ── summary 聚合端点 ────────────────────────────────────────────────────────
def test_summary_returns_all_blocks_with_disclaimer(client, monkeypatch):
    """summary 等价于 5 个端点合并，且带口径 disclaimer（前端单请求渲染用）。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "sm@test.com")
        _mk_song(db, u, status="completed")
        _mk_song(db, u, status="failed", note="供应商 502")
    finally:
        db.close()

    body = client.get("/api/admin/analytics/summary?days=7", headers=ADMIN).json()
    assert set(body) >= {"window_days", "generated_at", "overview", "providers", "failures", "funnel", "engagement", "disclaimer"}
    assert body["window_days"] == 7
    assert body["overview"]["completed"] == 1
    assert body["failures"]["total_failed"] == 1
    # 与单独端点数值一致（同口径，不是两套算法）
    solo = client.get("/api/admin/analytics/overview?days=7", headers=ADMIN).json()
    assert body["overview"]["completion_rate"] == solo["completion_rate"]


def test_analytics_endpoints_do_not_leak_pii(client, monkeypatch):
    """看板不得成为 PII 泄露面：响应里不得出现邮箱。"""
    monkeypatch.setattr(settings, "admin_token", "secret")
    db = SessionLocal()
    try:
        u = _mk_user(db, "leak@test.com")
        _mk_song(db, u, status="failed", note="供应商 502")
    finally:
        db.close()

    raw = client.get("/api/admin/analytics/summary?days=7", headers=ADMIN).text
    assert "leak@test.com" not in raw
    assert "hashed_password" not in raw
