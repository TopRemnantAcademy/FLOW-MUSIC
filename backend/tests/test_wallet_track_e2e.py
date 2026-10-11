"""P1 E2E：FLOW 钱包跨域「按次扣费」轨接线路径验证（接线已在 songs.py 落地）。

覆盖关键路径，全部走 conftest 的 MockProvider + 测试库，不触真实引擎/网络：
  1) HTTP 完整链路：超配额 + **用户显式同意** + 已关联 FLOW + 开关开 → 200（hold）→ 完成 → captured；
  2) 未显式同意（use_flow_credit=False/缺省）即使已关联 + 开关开也**不得扣款** → 402（防未经同意自动扣款）；
  3) 进程内确定性校验结算分支：completed→capture / 其余→release（避开 BackgroundTasks 时序）；
  4) 超配额 + 未关联 FLOW + 已同意 → 402，且无 Song 孤儿、无 reservation 残留。

开关默认关，本测试显式开启并在 finally 还原，确保零行为变更对其它测试无副作用。
"""

import asyncio

from fastapi.testclient import TestClient

from app.config import settings as _settings
from app.db import SessionLocal
from app.models import Song, User, UserLink
from app.wallet_track import WalletReservation, get_held_reservation_by_job, get_reservation
from app.routers.songs import _create_song, _settle_wallet_track, _process_song
from app.schemas import GenerateReq


def _register(client: TestClient, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _link(client: TestClient, h: dict, subscriber_id: str) -> None:
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post("/api/auth/flow-link", json={"subscriber_id": subscriber_id}, headers=h)
    assert r.status_code == 200, r.text


def _drive(song_id: int) -> None:
    """显式驱动与生产 BackgroundTasks 同一个处理函数，保证结算确定性。"""
    asyncio.run(_process_song(song_id))


def _resv(db, song_id: int) -> WalletReservation | None:
    return db.query(WalletReservation).filter_by(job_id=f"song:{song_id}").first()


def test_wallet_track_e2e_hold_then_capture(client: TestClient):
    saved_flag = _settings.enable_flow_wallet_track
    saved_quota = _settings.per_user_daily_quota
    _settings.enable_flow_wallet_track = True
    _settings.per_user_daily_quota = 0  # 零配额 → 任何生成都走超配额路径
    try:
        h = _register(client, "wt_e2e_cap@test.com")
        _link(client, h, "sub_e2e_cap")

        r = client.post(
            "/api/songs/generate",
            json={"prompt": "钱包轨捕获验证", "style": "pop", "use_flow_credit": True},
            headers=h,
        )
        assert r.status_code == 200, r.text  # 超配额但用户已同意 → hold 放行
        song_id = r.json()["song_id"]

        # 显式驱动后台任务（与生产 BackgroundTasks 同一函数）→ 完成 + 结算
        _drive(song_id)
        s = client.get(f"/api/songs/{song_id}", headers=h).json()
        assert s["status"] == "completed"

        # 结算 → captured（套利下限保证 amount_krw 折算 ≥ 单首价）
        with SessionLocal() as db:
            resv = _resv(db, song_id)
            assert resv is not None and resv.status == "captured"
            assert resv.amount_krw > 0
    finally:
        _settings.enable_flow_wallet_track = saved_flag
        _settings.per_user_daily_quota = saved_quota


def test_no_consent_never_charges_even_if_linked(client: TestClient):
    """🔒 关键合规回归：未显式同意 use_flow_credit → 即使已关联 FLOW 且开关开，
    也绝不能自动扣款（必须 402 + 无任何 reservation）。"""
    saved_flag = _settings.enable_flow_wallet_track
    saved_quota = _settings.per_user_daily_quota
    _settings.enable_flow_wallet_track = True
    _settings.per_user_daily_quota = 0
    try:
        h = _register(client, "wt_e2e_noconsent@test.com")
        _link(client, h, "sub_e2e_noconsent")

        # 未传 use_flow_credit（默认 False）
        r = client.post(
            "/api/songs/generate",
            json={"prompt": "未同意不应扣款", "style": "pop"},
            headers=h,
        )
        assert r.status_code == 402, r.text

        with SessionLocal() as db:
            # 绝不产生任何钱包预约（无同意 = 无扣款）
            assert db.query(WalletReservation).count() == 0
            u = db.query(User).filter_by(email="wt_e2e_noconsent@test.com").first()
            assert db.query(Song).filter(Song.user_id == u.id).count() == 0
    finally:
        _settings.enable_flow_wallet_track = saved_flag
        _settings.per_user_daily_quota = saved_quota


def test_wallet_track_inproc_settlement_branches(client: TestClient):
    """completed→capture / 其余→release。进程内直调 _create_song + _settle_wallet_track，
    确定性覆盖两条结算分支，不依赖 BackgroundTasks 的运行时序。"""
    saved_flag = _settings.enable_flow_wallet_track
    saved_quota = _settings.per_user_daily_quota
    _settings.enable_flow_wallet_track = True
    _settings.per_user_daily_quota = 0
    try:
        with SessionLocal() as db:
            u = User(email="wt_e2e_rel@test.com", hashed_password="x", tier_key="free")
            db.add(u)
            db.commit()
            db.refresh(u)
            db.add(UserLink(flow_subscriber_id="sub_e2e_rel", music_user_id=u.id))
            db.commit()

            # 第一首：建 Song + 超配额 hold（enforce_or_hold 内已 commit），失败分支 → release
            song = _create_song(
                GenerateReq(prompt="钱包轨退款验证", style="pop", use_flow_credit=True), u, db
            )
            rid = get_held_reservation_by_job(db, f"song:{song.id}").reservation_id
            assert get_reservation(db, rid).status == "held"

            song.status = "failed"
            db.commit()
            _settle_wallet_track(db, song)
            assert get_reservation(db, rid).status == "released"

            # 第二首：再建一首并置 completed → capture
            song2 = _create_song(
                GenerateReq(prompt="钱包轨捕获分支", style="pop", use_flow_credit=True), u, db
            )
            rid2 = get_held_reservation_by_job(db, f"song:{song2.id}").reservation_id
            song2.status = "completed"
            db.commit()
            _settle_wallet_track(db, song2)
            assert get_reservation(db, rid2).status == "captured"
    finally:
        _settings.enable_flow_wallet_track = saved_flag
        _settings.per_user_daily_quota = saved_quota


def test_over_quota_unlinked_returns_402_no_orphan(client: TestClient):
    saved_flag = _settings.enable_flow_wallet_track
    saved_quota = _settings.per_user_daily_quota
    _settings.enable_flow_wallet_track = True
    _settings.per_user_daily_quota = 0
    try:
        h = _register(client, "wt_e2e_402@test.com")
        # 故意不关联 FLOW subscriber（即使用户已同意也无法扣款）

        r = client.post(
            "/api/songs/generate",
            json={"prompt": "未关联应被拒", "style": "pop", "use_flow_credit": True},
            headers=h,
        )
        assert r.status_code == 402, r.text

        with SessionLocal() as db:
            u = db.query(User).filter_by(email="wt_e2e_402@test.com").first()
            # 不应留下任何 Song 孤儿
            assert db.query(Song).filter(Song.user_id == u.id).count() == 0
            # 也不应有任何 reservation（未关联 → 走 402，从未 hold）
            assert db.query(WalletReservation).count() == 0
    finally:
        _settings.enable_flow_wallet_track = saved_flag
        _settings.per_user_daily_quota = saved_quota
