"""P1 对账脚本：验证 5 条不变式的检测能力（不是只验证「干净时通过」）。"""

from datetime import datetime, timedelta, timezone

from app.config import settings as _settings
from app.db import SessionLocal
from app.models import Song, User
from app.wallet_track import WalletReservation, WALLET_WORKSPACE
from scripts.wallet_reconcile import reconcile


def _cleanup(db):
    db.query(WalletReservation).delete()
    db.commit()


def test_reconcile_clean_state_passes():
    with SessionLocal() as db:
        _cleanup(db)
        r = reconcile(db)
        assert r["ok"] is True
        assert r["issues"] == []
        assert r["summary"]["workspace"] == WALLET_WORKSPACE


def test_reconcile_detects_stale_hold():
    """I1：held 超过阈值（进程崩溃/未结算）必须被抓出。"""
    with SessionLocal() as db:
        _cleanup(db)
        db.add(
            WalletReservation(
                workspace_id=WALLET_WORKSPACE,
                subscriber_id="sub_rc1",
                job_id="song:999001",
                amount_krw=10,
                status="held",
                created_at=datetime.now(timezone.utc) - timedelta(hours=48),
            )
        )
        db.commit()
        r = reconcile(db, max_hold_hours=24)
        assert r["ok"] is False
        kinds = [i["check"] for i in r["issues"]]
        assert "I1_STALE_HOLD" in kinds
        _cleanup(db)


def test_reconcile_detects_settlement_gap():
    """I2：Song 已 completed 但预约仍 held → 本应 capture 却漏了。"""
    with SessionLocal() as db:
        _cleanup(db)
        u = User(email="rc2@test.com", hashed_password="x", tier_key="free")
        db.add(u)
        db.commit()
        db.refresh(u)
        song = Song(user_id=u.id, title="rc2", status="completed")
        db.add(song)
        db.commit()
        db.refresh(song)

        db.add(
            WalletReservation(
                workspace_id=WALLET_WORKSPACE,
                subscriber_id="sub_rc2",
                job_id=f"song:{song.id}",
                amount_krw=10,
                status="held",
            )
        )
        db.commit()
        r = reconcile(db)
        assert r["ok"] is False
        gap = [i for i in r["issues"] if i["check"] == "I2_SETTLEMENT_GAP"]
        assert gap and gap[0]["expected"] == "captured"
        _cleanup(db)


def test_reconcile_detects_duplicate_hold_and_bad_amount():
    """I3 重复 hold + I4 金额为 0/负 必须被抓出。"""
    with SessionLocal() as db:
        _cleanup(db)
        db.add(
            WalletReservation(
                workspace_id=WALLET_WORKSPACE,
                subscriber_id="sub_rc3",
                job_id="song:999003",
                amount_krw=10,
                status="held",
            )
        )
        db.add(
            WalletReservation(
                workspace_id=WALLET_WORKSPACE,
                subscriber_id="sub_rc3",
                job_id="song:999003",  # 同 job_id 第二条 held → 违反幂等
                amount_krw=0,  # 0 额 → 违反 I4
                status="held",
            )
        )
        db.commit()
        r = reconcile(db)
        kinds = {i["check"] for i in r["issues"]}
        assert "I3_DUPLICATE_HOLD" in kinds
        assert "I4_NONPOSITIVE_AMOUNT" in kinds
        _cleanup(db)


def test_reconcile_arbitrage_floor():
    """I5：折算回 CNY 低于单首价下限 → 套利风险，必须被抓出。"""
    saved = _settings.flow_wallet_per_song_cny
    _settings.flow_wallet_per_song_cny = 100.0  # 人为抬高下限
    try:
        with SessionLocal() as db:
            _cleanup(db)
            db.add(
                WalletReservation(
                    workspace_id=WALLET_WORKSPACE,
                    subscriber_id="sub_rc4",
                    job_id="song:999004",
                    amount_krw=1,  # 折算仅 0.19 CNY << 100
                    status="captured",
                )
            )
            db.commit()
            r = reconcile(db)
            assert "I5_ARBITRAGE_FLOOR" in {i["check"] for i in r["issues"]}
            _cleanup(db)
    finally:
        _settings.flow_wallet_per_song_cny = saved
