"""P1 基础件：FLOW 钱包跨域按次扣费轨（hold/capture/release + 套利下限）。

只测基础件本身（镜像模型 + 服务封装 + 汇率下限），不触碰生成端点（接线待拍板）。
全部走 conftest 的 /tmp/music_test_session.db，不依赖真实 Suno/网络。
"""

from app.db import SessionLocal
from app.config import settings as _settings
from app.models import Song, User, UserLink
from app.wallet_track import (
    WalletReservation,
    cny_to_krw,
    krw_to_cny,
    capture_wallet_track,
    hold_wallet_track,
    release_wallet_track,
)


def _make_user(sl, email):
    u = User(email=email, hashed_password="x")
    sl.add(u)
    sl.flush()
    return u


def _link(sl, user, sub_id):
    sl.add(UserLink(flow_subscriber_id=sub_id, music_user_id=user.id))


def test_fx_roundtrip_and_arbitrage_floor():
    # 折算价下限：ceil 保证 krw_to_cny(ceil(p)) >= p（防套利）
    for p in (0.26, 19.0, 39.0, 99.0, 299.0):
        krw = cny_to_krw(p)
        assert krw_to_cny(krw) >= p - 1e-9
    # 反向快照一致（误差 < 1 元）
    assert abs(krw_to_cny(cny_to_krw(39.0)) - 39.0) < 1.0


def test_hold_capture_release_state_machine():
    _settings.enable_flow_wallet_track = True
    try:
        with SessionLocal() as db:
            u = _make_user(db, "wt1@test.com")
            _link(db, u, "sub_wt1")
            db.commit()
            song = Song(user_id=u.id, title="t1")
            db.add(song)
            db.flush()

            rid = hold_wallet_track(db, music_user_id=u.id, song_id=song.id, price_cny=39.0)
            assert isinstance(rid, str) and len(rid) > 0
            rec = db.get(WalletReservation, rid)
            assert rec.status == "held"
            assert rec.amount_krw == cny_to_krw(39.0)
            assert rec.subscriber_id == "sub_wt1"
            assert rec.workspace_id == "music"
            assert rec.job_id == f"song:{song.id}"

            # 幂等：重复 hold 返回同一 reservation_id
            rid2 = hold_wallet_track(db, music_user_id=u.id, song_id=song.id, price_cny=39.0)
            assert rid2 == rid

            # capture → captured
            assert capture_wallet_track(db, rid) is True
            assert db.get(WalletReservation, rid).status == "captured"
            # capture 幂等
            assert capture_wallet_track(db, rid) is True

            # capture 后不可 release（已结算）
            try:
                release_wallet_track(db, rid)
                raise AssertionError("release after capture must be rejected")
            except ValueError:
                pass
            db.commit()
    finally:
        _settings.enable_flow_wallet_track = False


def test_release_on_failure_path():
    _settings.enable_flow_wallet_track = True
    try:
        with SessionLocal() as db:
            u = _make_user(db, "wt2@test.com")
            _link(db, u, "sub_wt2")
            db.commit()
            song = Song(user_id=u.id, title="t2")
            db.add(song)
            db.flush()

            rid = hold_wallet_track(db, music_user_id=u.id, song_id=song.id, price_cny=99.0)
            # 生成失败 → release（退款）
            assert release_wallet_track(db, rid) is True
            assert db.get(WalletReservation, rid).status == "released"
            # release 幂等
            assert release_wallet_track(db, rid) is True
            # released 后不可 capture
            try:
                capture_wallet_track(db, rid)
                raise AssertionError("capture after release must be rejected")
            except ValueError:
                pass
            db.commit()
    finally:
        _settings.enable_flow_wallet_track = False


def test_unlinked_user_rejected():
    _settings.enable_flow_wallet_track = True
    try:
        with SessionLocal() as db:
            u = _make_user(db, "wt3@test.com")  # 未关联 FLOW subscriber
            db.commit()
            song = Song(user_id=u.id, title="t3")
            db.add(song)
            db.flush()
            try:
                hold_wallet_track(db, music_user_id=u.id, song_id=song.id, price_cny=39.0)
                raise AssertionError("unlinked user must be rejected")
            except ValueError:
                pass
    finally:
        _settings.enable_flow_wallet_track = False


def test_disabled_flag_blocks():
    # 默认关：hold 必须拒绝，零行为变更
    with SessionLocal() as db:
        u = _make_user(db, "wt4@test.com")
        _link(db, u, "sub_wt4")
        db.commit()
        song = Song(user_id=u.id, title="t4")
        db.add(song)
        db.flush()
        try:
            hold_wallet_track(db, music_user_id=u.id, song_id=song.id, price_cny=39.0)
            raise AssertionError("disabled flag must block hold")
        except RuntimeError:
            pass
