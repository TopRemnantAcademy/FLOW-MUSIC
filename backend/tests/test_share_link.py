"""GAP-013 无状态分享链接测试。

覆盖 issue 明确要求的**四类**：
  1. 过期（expired）
  2. 篡改（tampered）
  3. 非公开（not public）
  4. 未过审（not approved）

另加：无状态性（无表/无迁移）、身份隔离（A 不能给 B 的歌签票）、
匿名可访问、以及「分享不绕开 freemium 门控」。
"""

import contextlib
import os
import time

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.routers.share import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    _b64d,
    _b64e,
    share_secret,
    sign_share_token,
    verify_share_token,
)


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


def _mk_song(db, user_id: int, **kw) -> Song:
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "分享测试曲"),
        status=kw.pop("status", "completed"),
        audio_url=kw.pop("audio_url", "https://upstream.test/full.mp3"),
        audio_cdn_url=kw.pop("audio_cdn_url", "https://cdn.test/full.mp3"),
        preview_audio_url=kw.pop("preview_audio_url", "https://cdn.test/preview.mp3"),
        preview_seconds=kw.pop("preview_seconds", 60),
        moderation_status=kw.pop("moderation_status", "approved"),
        is_public=kw.pop("is_public", True),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ═══════════════════════════════════════════════════════════════════
# 0. token 原语：签名 / 往返 / 密钥来源
# ═══════════════════════════════════════════════════════════════════
def test_sign_and_verify_roundtrip():
    tok = sign_share_token(42, ttl_seconds=3600)
    claims = verify_share_token(tok)
    assert claims.song_id == 42
    assert claims.exp > int(time.time())


def test_secret_falls_back_to_jwt_secret(monkeypatch):
    """未配 SHARE_SECRET → 回落 jwt_secret（不配也能用）。"""
    monkeypatch.setattr(settings, "share_secret", "")
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-jwt")
    assert share_secret() == "unit-test-jwt"


def test_secret_prefers_share_secret(monkeypatch):
    monkeypatch.setattr(settings, "share_secret", "dedicated-share-key")
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-jwt")
    assert share_secret() == "dedicated-share-key"


def test_ttl_is_clamped_to_max():
    """超长 ttl 被夹到 30 天上限（防「分享即永久发布」）。"""
    now = 1_700_000_000
    tok = sign_share_token(1, ttl_seconds=MAX_TTL_SECONDS * 100, now=now)
    assert verify_share_token(tok, now=now).exp == now + MAX_TTL_SECONDS


def test_token_is_url_safe_and_pair_shaped():
    """token 直接进 URL path，故必须是 url-safe base64 且形如 body.sig。"""
    tok = sign_share_token(7)
    assert tok.count(".") == 1
    assert all(c.isalnum() or c in "-_." for c in tok), tok


def test_no_database_table_added():
    """无状态铁律：不得为分享新增任何表（本批仅 #91 允许迁移）。"""
    from app.db import Base

    assert "shares" not in Base.metadata.tables
    assert "share_links" not in Base.metadata.tables
    assert not any("share" in t for t in Base.metadata.tables)


# ═══════════════════════════════════════════════════════════════════
# 1. 过期（expired）
# ═══════════════════════════════════════════════════════════════════
def test_expired_token_is_rejected():
    """exp 已过 → 403（构造一个「一小时前就过期」的 token）。"""
    past = int(time.time()) - 3600
    tok = sign_share_token(5, ttl_seconds=60, now=past)
    # 该 token 本身是可验证的（过去签的，签名有效），只是 exp 已过。
    # 为断言「确实是过期而非签名坏」，这里用同一个 now=past 验一次。
    assert verify_share_token(tok, now=past).song_id == 5
    with pytest.raises(Exception) as ei:
        verify_share_token(tok)
    assert getattr(ei.value, "status_code", None) == 403


def test_expired_token_rejected_over_http(client):
    email = "share-exp@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email))
        tok = sign_share_token(song.id, ttl_seconds=60, now=int(time.time()) - 3600)
    finally:
        db.close()
    # 匿名访问（分享端点是匿名的）
    r = client.get(f"/api/share/songs/{tok}")
    assert r.status_code == 403, r.text
    assert "过期" in r.json()["detail"] or "无效" in r.json()["detail"]


def test_boundary_exp_now_equals_exp_is_expired():
    """边界：exp == now 即视为过期（`>` 而非 `>=` 会让临界点多活一秒）。"""
    now = 1_800_000_000
    tok = sign_share_token(3, ttl_seconds=60, now=now - 60)
    with pytest.raises(Exception) as ei:
        verify_share_token(tok, now=now)
    assert getattr(ei.value, "status_code", None) == 403


def test_just_before_expiry_still_valid():
    now = 1_800_000_000
    tok = sign_share_token(3, ttl_seconds=60, now=now - 59)
    assert verify_share_token(tok, now=now).song_id == 3


# ═══════════════════════════════════════════════════════════════════
# 2. 篡改（tampered）
# ═══════════════════════════════════════════════════════════════════
def test_tampered_song_id_is_rejected():
    """核心攻击：改载荷里的 sid 去偷看别人的作品 —— 签名会失配。"""
    tok = sign_share_token(5, ttl_seconds=3600)
    body, sig = tok.split(".", 1)
    import json

    payload = json.loads(_b64d(body).decode())
    payload["sid"] = 999  # 攻击者改成另一首
    forged_body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    forged = f"{forged_body}.{sig}"  # 沿用原签名
    with pytest.raises(Exception) as ei:
        verify_share_token(forged)
    assert getattr(ei.value, "status_code", None) == 403


def test_tampered_expiry_is_rejected():
    """改 exp 把过期票续命 —— 同样失配。"""
    import json

    tok = sign_share_token(5, ttl_seconds=60, now=int(time.time()) - 7200)
    body, sig = tok.split(".", 1)
    payload = json.loads(_b64d(body).decode())
    payload["exp"] = int(time.time()) + 99999
    forged_body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    with pytest.raises(Exception) as ei:
        verify_share_token(f"{forged_body}.{sig}")
    assert getattr(ei.value, "status_code", None) == 403


def test_tampered_signature_is_rejected():
    tok = sign_share_token(5, ttl_seconds=3600)
    body, sig = tok.split(".", 1)
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(Exception) as ei:
        verify_share_token(f"{body}.{flipped}")
    assert getattr(ei.value, "status_code", None) == 403


def test_malformed_tokens_rejected():
    """结构垃圾不得 500，一律 403。"""
    for bad in ["", ".", "abc", "a.b.c", "!!!.???", "e30.e30"]:
        with pytest.raises(Exception) as ei:
            verify_share_token(bad)
        assert getattr(ei.value, "status_code", None) == 403, bad


def test_token_signed_with_other_secret_is_rejected(monkeypatch):
    """换了密钥签的票 → 403（轮换 SHARE_SECRET 即作废全部旧分享链接）。"""
    tok = sign_share_token(5, ttl_seconds=3600)
    monkeypatch.setattr(settings, "share_secret", "a-completely-different-key")
    with pytest.raises(Exception) as ei:
        verify_share_token(tok)
    assert getattr(ei.value, "status_code", None) == 403


def test_sql_injection_shaped_song_id_rejected():
    """载荷里塞非数字 sid → 403（不是 500）。"""
    import json

    from app.routers.share import verify_share_token as v
    import hashlib
    import hmac as _hmac

    payload = {"v": "v1", "sid": "1 OR 1=1", "exp": int(time.time()) + 600, "iat": int(time.time())}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = _hmac.new(share_secret().encode(), body, hashlib.sha256).digest()
    with pytest.raises(Exception) as ei:
        v(f"{_b64e(body)}.{_b64e(sig)}")
    assert getattr(ei.value, "status_code", None) == 403


# ═══════════════════════════════════════════════════════════════════
# 3. 非公开（not public）
# ═══════════════════════════════════════════════════════════════════
def test_private_song_share_token_cannot_be_minted(client):
    """作者本人也不能给**未公开**的作品发分享票（否则「未公开」形同虚设）。"""
    email = "share-priv@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=False)
        sid = song.id
    finally:
        db.close()
    r = client.post(f"/api/share/songs/{sid}", json={}, headers=h)
    assert r.status_code == 400, r.text


def test_revoked_public_share_link_dies_immediately(client):
    """关键：链接发出后作者撤回公开 → 旧链接**立刻**失效（访问时刻实时判定）。"""
    email = "share-revoke@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True)
        sid = song.id
    finally:
        db.close()
    # 1) 作者签发成功
    r = client.post(f"/api/share/songs/{sid}", json={}, headers=h)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # 2) 匿名可访问
    assert client.get(f"/api/share/songs/{token}").status_code == 200
    # 3) 作者撤回公开
    db = SessionLocal()
    try:
        s = db.get(Song, sid)
        s.is_public = False
        db.commit()
    finally:
        db.close()
    # 4) 旧链接失效（404 — 不泄露存在性）
    assert client.get(f"/api/share/songs/{token}").status_code == 404


def test_private_song_token_hand_forged_still_404(client):
    """即使有人拿到密钥自己签一张「非公开作品」的票，内容闸仍拦下（404）。"""
    email = "share-priv2@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=False)
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    r = client.get(f"/api/share/songs/{tok}")
    assert r.status_code == 404, r.text


def test_soft_deleted_song_404(client):
    """软删（回收站）的作品：连作者自己都 404，分享链接更不该放行。"""
    import datetime

    email = "share-del@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True)
        tok = sign_share_token(song.id, ttl_seconds=3600)
        song.deleted_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").status_code == 404


def test_incomplete_song_404(client):
    """status != completed → 404（生成中的作品没有可播音频）。"""
    email = "share-inc@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, status="processing")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").status_code == 404


# ═══════════════════════════════════════════════════════════════════
# 4. 未过审（not approved）
# ═══════════════════════════════════════════════════════════════════
def test_rejected_song_cannot_mint_link(client):
    email = "share-rej@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, moderation_status="rejected")
        sid = song.id
    finally:
        db.close()
    r = client.post(f"/api/share/songs/{sid}", json={}, headers=h)
    assert r.status_code == 403, r.text


def test_rejected_song_token_404_at_access(client):
    """已发出的票 + 作品后来被驳回 → 访问 404。"""
    email = "share-rej2@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, moderation_status="approved")
        tok = sign_share_token(song.id, ttl_seconds=3600)
        song.moderation_status = "rejected"
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").status_code == 404


def test_pending_moderation_blocked_when_gate_on(client):
    """机审开启时 moderation=pending 不可分享（防未审内容外流）。"""
    assert settings.enable_moderation is True  # conftest 已置 True
    email = "share-pend@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, moderation_status="pending")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").status_code == 404


def test_pending_allowed_when_moderation_off(client, monkeypatch):
    """机审关闭时 pending 不再拦（与 plaza._public_filters 同一套条件）。"""
    monkeypatch.setattr(settings, "enable_moderation", False)
    email = "share-pend2@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, moderation_status="pending")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 5. 签发权限 + 匿名可访问 + 不绕开 freemium 门
# ═══════════════════════════════════════════════════════════════════
def test_mint_requires_login(client):
    """未认证 → 401。

    ⚠️ 必须清 cookie：`/api/auth/register` 会**下发 HttpOnly cookie**（[SEC] 2026-09-13
    的 cookie 认证改造），而 `get_current_user` 是 header **或** cookie 取令牌。
    不清就会带着刚注册的登录态，测出假的 200（作者已过期的无头假设）。
    """
    email = "share-auth@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        sid = _mk_song(db, _uid(db, email)).id
    finally:
        db.close()
    client.cookies.clear()
    assert client.post(f"/api/share/songs/{sid}", json={}).status_code == 401


def test_mint_by_non_owner_404(client):
    """A 不能给 B 的（即使已公开）作品签票 —— 404，不泄露存在性。"""
    email_a, email_b = "share-a@test.com", "share-b@test.com"
    _register(client, email_a)
    hb = _register(client, email_b)
    db = SessionLocal()
    try:
        sid = _mk_song(db, _uid(db, email_a)).id
    finally:
        db.close()
    assert client.post(f"/api/share/songs/{sid}", json={}, headers=hb).status_code == 404


def test_mint_by_owner_succeeds_and_path_matches(client):
    email = "share-owner@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        sid = _mk_song(db, _uid(db, email)).id
    finally:
        db.close()
    r = client.post(f"/api/share/songs/{sid}", json={}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["path"] == f"/share/{d['token']}"
    assert d["expires_at"] > int(time.time())
    assert DEFAULT_TTL_SECONDS - 60 <= d["expires_at"] - int(time.time()) <= DEFAULT_TTL_SECONDS + 60


def test_anonymous_can_resolve_without_login(client):
    """未登录浏览器也能播：不带任何 Authorization 头 / cookie 即可解析。"""
    email = "share-anon@test.com"
    _register(client, email)
    client.cookies.clear()  # 真匿名：注册留下的 HttpOnly cookie 也要清掉
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), title="匿名可听")
        tok = sign_share_token(song.id, ttl_seconds=3600)
        sid = song.id
    finally:
        db.close()
    r = client.get(f"/api/share/songs/{tok}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["song_id"] == sid
    assert d["title"] == "匿名可听"


def test_anonymous_gets_preview_not_full_audio(client):
    """铁律：分享**不绕开** freemium 门 —— 匿名/未订阅拿到的是试听片段。"""
    email = "share-gate@test.com"
    _register(client, email)
    client.cookies.clear()  # 真匿名（free 档），否则会带上注册账号的 cookie
    db = SessionLocal()
    try:
        song = _mk_song(
            db,
            _uid(db, email),
            audio_url="https://upstream.test/full.mp3",
            audio_cdn_url="https://cdn.test/full.mp3",
            preview_audio_url="https://cdn.test/preview.mp3",
        )
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    d = client.get(f"/api/share/songs/{tok}").json()
    assert d["can_play_full"] is False
    assert d["audio_url"] == "https://cdn.test/preview.mp3"
    assert "full.mp3" not in (d["audio_url"] or "")
    assert d["preview_seconds"] == 60


def test_preview_missing_fails_closed(client):
    """片段缺失 → audio_url=None（绝不回退整曲链接）。"""
    email = "share-fc@test.com"
    _register(client, email)
    client.cookies.clear()
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), preview_audio_url="")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    d = client.get(f"/api/share/songs/{tok}").json()
    assert d["audio_url"] is None
    assert d["can_play_full"] is False


def test_share_view_does_not_leak_owner_only_fields(client):
    """精简视图：不下发 cost_cny / is_owner / play_count 等。

    这是 GAP-016 里同类问题（cost_cny 曾随匿名广场响应外泄）的**同类防线**，
    分享端点作为新的匿名面必须一并收口。
    """
    email = "share-leak@test.com"
    _register(client, email)
    client.cookies.clear()
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), cost_cny=1.23, play_count=99, prompt="内部提示词")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    d = client.get(f"/api/share/songs/{tok}").json()
    for k in ("cost_cny", "is_owner", "play_count", "prompt", "lyric", "custom_id", "user_id"):
        assert k not in d, k


def test_license_is_exposed_for_share(client):
    """GAP-016 联动：授权类型随分享下发（下载/使用边界对听众可见）。"""
    email = "share-lic@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), license="commercial")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}").json()["license"] == "commercial"


def test_two_songs_same_owner_tokens_are_independent(client):
    """同一作者的 A/B 两首：各自 token 只解析到各自那首（无串台）。"""
    email = "share-two@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        sa = _mk_song(db, uid, title="甲")
        sb = _mk_song(db, uid, title="乙")
        ta, tb = sign_share_token(sa.id), sign_share_token(sb.id)
        ia, ib = sa.id, sb.id
    finally:
        db.close()
    da = client.get(f"/api/share/songs/{ta}").json()
    db_ = client.get(f"/api/share/songs/{tb}").json()
    assert (da["song_id"], db_["song_id"]) == (ia, ib)
    assert da["title"] == "甲" and db_["title"] == "乙"


# ═══════════════════════════════════════════════════════════════════
# 6. 波形峰值端点（GAP-013）
# ═══════════════════════════════════════════════════════════════════
def test_extract_peaks_from_generated_wav(tmp_path):
    """峰值抽取器对可解码音频产出合理包络（不是全 0、不是全 1）。"""
    import math
    import struct
    import wave

    from app.routers.share import _extract_peaks

    p = tmp_path / "tone.wav"
    sr = 8000
    with wave.open(str(p), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(sr * 2):
            # 幅度包络 0.2~0.9：抽取后应当能看到明显起伏，而不是一条直线
            env = 0.2 + 0.7 * abs(math.sin(2 * math.pi * i / sr / 0.5))
            frames += struct.pack("<h", int(32000 * env * math.sin(2 * math.pi * 440 * i / sr)))
        w.writeframes(bytes(frames))

    peaks = _extract_peaks(str(p), 64)
    assert len(peaks) == 64
    assert all(0.0 <= x <= 1.0 for x in peaks)
    assert max(peaks) > 0.5, "峰值上限过低说明抽取没读到真实样本"
    assert min(peaks) < max(peaks), "包络应有起伏（恒定值说明丢包络）"


def test_extract_peaks_rejects_non_audio(tmp_path):
    """非音频文件 → 抛错（fail-closed，不返回全 0 的假波形）。"""
    from app.routers.share import _extract_peaks

    p = tmp_path / "junk.mp3"
    p.write_bytes(b"this is definitely not audio")
    with pytest.raises(Exception):
        _extract_peaks(str(p), 32)


def test_peaks_requires_valid_token(client):
    """peaks 与分享本体**同权限**：坏 token 一律 403（不是 200 空数组）。"""
    assert client.get("/api/share/songs/garbage.token/peaks").status_code == 403


def test_peaks_expired_token_403(client):
    tok = sign_share_token(1, ttl_seconds=60, now=int(time.time()) - 3600)
    assert client.get(f"/api/share/songs/{tok}/peaks").status_code == 403


def test_peaks_private_song_404(client):
    """未公开作品的 peaks 不给 —— 不成为探测未公开内容的旁路。"""
    email = "peaks-priv@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=False)
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}/peaks").status_code == 404


def test_peaks_rejected_song_404(client):
    email = "peaks-rej@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), is_public=True, moderation_status="rejected")
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}/peaks").status_code == 404


def test_peaks_no_audio_source_404(client):
    """没有可下载源 → 404（fail-closed，不编峰值）。

    ⚠️ 必须先清峰值缓存：缓存键是 `{song_id}.{buckets}.json`，而 SQLite 每个测试
    重建后 song id 又从 1 开始 —— 本机开发时留下的 `1.800.json` 会让这个用例
    「命中」上一首歌的缓存而误判 200。测试必须自带确定性，不能依赖作业目录干净。
    """
    email = "peaks-noaudio@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), audio_url="", audio_cdn_url="")
        tok = sign_share_token(song.id, ttl_seconds=3600)
        sid = song.id
    finally:
        db.close()
    from app.routers.share import PEAKS_DEFAULT_BUCKETS, _peaks_cache_path

    with contextlib.suppress(OSError):
        os.unlink(_peaks_cache_path(sid, PEAKS_DEFAULT_BUCKETS))
    assert client.get(f"/api/share/songs/{tok}/peaks").status_code == 404


def test_peaks_buckets_param_bounds(client):
    """buckets 越界 → 422（FastAPI Query 约束），不静默夹取。"""
    email = "peaks-b@test.com"
    _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email))
        tok = sign_share_token(song.id, ttl_seconds=3600)
    finally:
        db.close()
    assert client.get(f"/api/share/songs/{tok}/peaks?buckets=1").status_code == 422
    assert client.get(f"/api/share/songs/{tok}/peaks?buckets=99999").status_code == 422


def test_peaks_and_view_are_same_permission(client):
    """同权限断言：本体 403/404 时 peaks 必须也是 403/404（无权限差）。"""
    expired = sign_share_token(1, ttl_seconds=60, now=int(time.time()) - 100)
    assert client.get(f"/api/share/songs/{expired}").status_code == 403
    assert client.get(f"/api/share/songs/{expired}/peaks").status_code == 403
    # 篡改签名：把最后一个字符换成「另一个」字符。直接 [:-1]+"X" 有个陷阱 ——
    # 原签名末位本身就可能是 "X"，那样 token 没变，测的就是「有效 token」了。
    body, sig = sign_share_token(1, ttl_seconds=3600).split(".", 1)
    forged = f"{body}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}"
    assert forged != f"{body}.{sig}"
    assert client.get(f"/api/share/songs/{forged}").status_code == 403
    assert client.get(f"/api/share/songs/{forged}/peaks").status_code == 403
