"""GAP-021 收尾：生成完成通知的「偏好开关 + 接线 + 关键路径不破」回归面。

本文件钉住的是此前"有能力没接线"的缺口（notify_generation_done 已存在但全仓库无
生产调用），现在把它接线到生成完成回调（routers/songs.py:_finalize_song），且严格
门控在用户偏好开关上。每一条都对应一个"接线后才会暴露"的具体失败：

1. **默认 opt-in = False。** 新用户默认收不到生成完成通知 —— 避免无条件邮件骚扰 +
   未配置 SES 时的无效发信成本。注册后 /me 与 /preferences 都必须反映 False。
2. **偏好可开关。** PATCH /preferences 能翻转，且 /me、GET /preferences 同步反映。
3. **关 → 不通知。** 偏好关时，即使生成 completed，_finalize_song 也绝不该调
   notify_generation_done（否则回到"无条件骚扰"原罪）。
4. **开 + completed → 通知。** 偏好开且生成成功，必须调一次 notify_generation_done。
5. **failed 不通知。** 即使偏好开，生成失败也不该通知（没人想收"失败了"的邮件轰炸）。
6. **通知崩溃不影响作品。** notify_generation_done 抛异常时，_finalize_song 必须
   吞掉、绝不把"生成成功"变成"生成失败"或拖慢关键路径。
7. **迁移单 head。** l1a2b3c4d5e6 必须是唯一 head 且链在 k1a2b3c4d5e6 之后，
   否则 `alembic check` 会漂移、生产升级会断链。
"""

import asyncio
import os
import glob

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.orchestrator.base import SongResult


# 固定的测试端点（不触网，与 test_notifications.py 保持一致）。
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123-def456"
KEYS = {"p256dh": "B" * 87, "auth": "a" * 22}


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ───────────────────────── 1. 默认 opt-in = False ─────────────────────────


def test_fresh_user_defaults_notify_off_via_me(client):
    h = _register(client, "default-off@test.com")
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 200
    assert r.json()["notify_generation_done_enabled"] is False


def test_fresh_user_defaults_notify_off_via_prefs(client):
    h = _register(client, "default-off2@test.com")
    r = client.get("/api/notifications/preferences", headers=h)
    assert r.status_code == 200
    assert r.json()["notify_generation_done_enabled"] is False


# ───────────────────────── 2. 偏好可开关 ─────────────────────────


def test_preferences_patch_roundtrip(client):
    h = _register(client, "toggle@test.com")
    # 开
    r = client.patch(
        "/api/notifications/preferences",
        headers=h,
        json={"notify_generation_done_enabled": True},
    )
    assert r.status_code == 200
    assert r.json()["notify_generation_done_enabled"] is True
    # GET 反映
    assert client.get("/api/notifications/preferences", headers=h).json()[
        "notify_generation_done_enabled"
    ] is True
    # /me 同步反映
    assert client.get("/api/auth/me", headers=h).json()[
        "notify_generation_done_enabled"
    ] is True
    # 关
    r = client.patch(
        "/api/notifications/preferences",
        headers=h,
        json={"notify_generation_done_enabled": False},
    )
    assert r.json()["notify_generation_done_enabled"] is False
    assert client.get("/api/notifications/preferences", headers=h).json()[
        "notify_generation_done_enabled"
    ] is False


def test_preferences_requires_auth(client):
    r = client.get("/api/notifications/preferences")
    assert r.status_code == 401
    r = client.patch(
        "/api/notifications/preferences",
        json={"notify_generation_done_enabled": True},
    )
    assert r.status_code == 401


# ───────────────────────── 3. 接线门控（_finalize_song） ─────────────────────────


def _make_user(db, email: str, notify: bool) -> User:
    u = User(
        email=email,
        hashed_password="x",
        notify_generation_done_enabled=notify,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_song(db, user: User, title: str = "晨光") -> Song:
    s = Song(user_id=user.id, title=title, status="processing", moderation_status="pending")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _isolate_finalize(monkeypatch):
    """把 _finalize_song 里的副作用全部关掉，只暴露「通知门控」这一条决策。"""
    # 这些分支默认由 settings 决定，显式关掉以保证用例不依赖默认值/不触网。
    for name in (
        "enable_audio_postprocess",
        "enable_preview_clip",
        "enable_audio_moderation",
        "enable_cdn_mirror_on_generate",
    ):
        monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(
        "app.routers.songs.sync_song_tags", lambda db, song: None
    )
    monkeypatch.setattr(
        "app.routers.songs._enqueue_webhook_deliveries", lambda song, db: None
    )


def test_finalize_does_not_notify_when_pref_off(monkeypatch):
    """偏好关 → 即便 completed，也绝不调 notify_generation_done。"""
    _isolate_finalize(monkeypatch)
    sent = []
    monkeypatch.setattr(
        "app.routers.notifications.notify_generation_done",
        lambda db, user, song_id, title: sent.append((song_id, title)) or [],
    )
    db = SessionLocal()
    try:
        u = _make_user(db, "off@test.com", notify=False)
        s = _make_song(db, u)
        asyncio.run(_finalize_song(s, db, SongResult(status="completed")))
    finally:
        db.close()
    assert sent == [], "偏好关时不应发出任何生成完成通知"


def test_finalize_notifies_when_pref_on_and_completed(monkeypatch):
    """偏好开 + completed → 必须调一次 notify_generation_done。"""
    _isolate_finalize(monkeypatch)
    sent = []
    monkeypatch.setattr(
        "app.routers.notifications.notify_generation_done",
        lambda db, user, song_id, title: sent.append((song_id, title)) or [],
    )
    db = SessionLocal()
    try:
        u = _make_user(db, "on@test.com", notify=True)
        s = _make_song(db, u, title="晚祷")
        asyncio.run(_finalize_song(s, db, SongResult(status="completed")))
    finally:
        db.close()
    assert len(sent) == 1, "偏好开且 completed 时必须通知一次"
    assert sent[0][0] == s.id and sent[0][1] == "晚祷"


def test_finalize_does_not_notify_on_failed(monkeypatch):
    """偏好开但生成失败 → 不通知（避免"失败"邮件轰炸）。"""
    _isolate_finalize(monkeypatch)
    sent = []
    monkeypatch.setattr(
        "app.routers.notifications.notify_generation_done",
        lambda db, user, song_id, title: sent.append((song_id, title)) or [],
    )
    db = SessionLocal()
    try:
        u = _make_user(db, "on-fail@test.com", notify=True)
        s = _make_song(db, u)
        asyncio.run(_finalize_song(s, db, SongResult(status="failed")))
    finally:
        db.close()
    assert sent == [], "生成失败不应发通知"


def test_finalize_survives_notify_exception(monkeypatch):
    """notify_generation_done 抛异常 → _finalize_song 必须吞掉、不向上抛。"""
    _isolate_finalize(monkeypatch)

    def _boom(db, user, song_id, title):
        raise RuntimeError("SES 炸了")

    monkeypatch.setattr(
        "app.routers.notifications.notify_generation_done", _boom
    )
    db = SessionLocal()
    try:
        u = _make_user(db, "boom@test.com", notify=True)
        s = _make_song(db, u)
        # 不应抛异常；作品状态保持 completed 路径（moderation 置 approved）。
        # 注意：传进来的 s 被 _finalize_song 就地改写（同一对象），无需 commit/refresh。
        asyncio.run(_finalize_song(s, db, SongResult(status="completed")))
        assert s.moderation_status == "approved", "通知崩溃不应阻断收尾（应已自动过审）"
    finally:
        db.close()


# ───────────────────────── 4. 迁移单 head ─────────────────────────


def test_migration_single_head_after_gap021():
    """l1a2b3c4d5e6 必须是唯一 head，且挂在 k1a2b3c4d5e6 之后。"""
    versions_dir = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions")
    revisions = {}  # rev -> list[down_rev]
    for path in glob.glob(os.path.join(versions_dir, "*.py")):
        text = open(path, encoding="utf-8").read()
        rev = None
        downs = []
        for line in text.splitlines():
            ls = line.strip()
            if ls.startswith("revision") and "=" in ls and "down_revision" not in ls:
                rev = ls.split("=", 1)[1].strip().strip('"').strip("'")
            if ls.startswith("down_revision") and "=" in ls:
                val = ls.split("=", 1)[1].strip().strip('"').strip("'").strip("()[]")
                if val and val.lower() != "none":
                    downs = [v.strip().strip('"').strip("'") for v in val.split(",") if v.strip()]
        if rev:
            revisions[rev] = downs

    referenced = set()
    for downs in revisions.values():
        referenced.update(downs)
    heads = [r for r in revisions if r not in referenced]

    assert heads == ["l1a2b3c4d5e6"], f"期望唯一 head=l1a2b3c4d5e6，实际: {heads}"
    assert revisions["l1a2b3c4d5e6"] == ["k1a2b3c4d5e6"], "新迁移必须挂在 k1a2b3c4d5e6 之后"
    assert "k1a2b3c4d5e6" in referenced, "旧 head k1a2b3c4d5e6 应被新迁移引用（不再是 head）"


# _finalize_song 是 songs 模块内部函数（接线目标），测试需要直接调用它。
from app.routers.songs import _finalize_song  # noqa: E402  (置于尾部仅为聚合阅读)
