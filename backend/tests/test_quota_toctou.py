"""回归测试：每日配额并发（TOCTOU）防护。

入口 `enforce_generate_limits` 是「先读计数 → 放行」，与 Song 插入之间存在并发窗口：
并发请求各自读到未超限的旧计数而全部放行 → 超额作品照样派发供应商（Mureka 按首计费）。
`_new_song_row` 在提交后**复查**并在调用方 `bg.add_task`(派发) 之前撤销，闭掉这条烧钱路径。
"""

import pytest
from fastapi import HTTPException

from app.db import SessionLocal
from app.entitlements import resolve_limits
from app.models import Song, User
from app.routers.songs import _new_song_row


def _mk_user(email: str) -> User:
    db = SessionLocal()
    try:
        u = User(email=email, hashed_password="x", tier_key="free")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


def _seed_songs(user_id: int, n: int) -> None:
    db = SessionLocal()
    try:
        for i in range(n):
            db.add(
                Song(
                    user_id=user_id,
                    title=f"seeded-{i}",
                    status="completed",
                    task_type="generate",
                )
            )
        db.commit()
    finally:
        db.close()


def test_new_song_row_rolls_back_over_quota_row():
    """模拟并发窗口：预检读到旧计数放行，插入后复查发现超额 → 402 且不留残留行。"""
    db = SessionLocal()
    try:
        user = _mk_user("toctou@example.com")
        quota = int(resolve_limits(db, user.tier_key)["daily_quota"])
        _seed_songs(user.id, quota)
        assert db.query(Song).filter(Song.user_id == user.id).count() == quota

        with pytest.raises(HTTPException) as exc:
            _new_song_row(user, db, title="over-quota", task="generate", prompt="p")
        assert exc.value.status_code == 402

        # 超额作品必须被撤销：残留 = 照样派发供应商 = 白烧钱
        db.expire_all()
        assert db.query(Song).filter(Song.user_id == user.id).count() == quota
    finally:
        db.close()
