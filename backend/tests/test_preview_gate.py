"""Freemium 试听门控测试（用户 2026-09-04 确认的业务规则）。

规则：
- 生成**始终是完整歌曲**（Mureka 无 duration 参数）；
- 门控只在分发环节：未订阅者只拿「前 N 秒试听片段」链接，订阅者拿完整音频；
- 订阅者可下载完整音频；未订阅 403；
- 片段缺失时未订阅者拿到 audio_url=None（fail-closed，绝不回退完整链接）。
"""

import contextlib
import glob
import math
import os
import shutil
import struct
import tempfile
import time
import wave

import pytest

from app import postprocess
from app.db import SessionLocal
from app.entitlements import is_subscribed
from app.models import Song, User


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_song(db, user_id: int, audio: str, preview: str, seconds=60, **kw) -> Song:
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "试听门控测试曲"),
        status="completed",
        audio_url=audio,
        preview_audio_url=preview,
        preview_seconds=seconds,
        moderation_status="approved",
        is_public=kw.pop("is_public", False),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


# ---------- 1. 订阅判定矩阵 ----------
def test_is_subscribed_matrix():
    assert is_subscribed(None) is False
    assert is_subscribed("") is False
    assert is_subscribed("free") is False
    assert is_subscribed("standard") is True
    assert is_subscribed("sacred") is True
    assert is_subscribed("enterprise") is True
    # 未知档位 → 未订阅（fail-safe，与 resolve_limits unknown→free 回落一致）
    assert is_subscribed("no-such-tier") is False


# ---------- 2. 未订阅：只给试听片段 ----------
def test_free_user_gets_preview_only(client):
    email = "gate-free@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db, uid, audio="https://cdn.test/full.mp3", preview="https://cdn.test/preview.mp3"
        )
    finally:
        db.close()

    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # 关键：未订阅者拿到的播放链接 = 片段，完整链接不得泄露
    assert body["audio_url"] == "https://cdn.test/preview.mp3"
    assert body["can_play_full"] is False
    assert body["can_download"] is False
    assert body["preview_seconds"] == 60


# ---------- 3. 订阅：完整音频 + 可下载 ----------
def test_subscriber_gets_full_audio(client):
    email = "gate-sub@test.com"
    h = _register(client, email)
    r = client.post("/api/auth/subscribe", json={"tier_key": "standard"}, headers=h)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db, uid, audio="https://cdn.test/full.mp3", preview="https://cdn.test/preview.mp3"
        )
    finally:
        db.close()

    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audio_url"] == "https://cdn.test/full.mp3"
    assert body["can_play_full"] is True
    assert body["can_download"] is True


# ---------- 4. 片段缺失 → fail-closed（绝不回退完整链接） ----------
def test_missing_preview_is_fail_closed(client):
    email = "gate-nopreview@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(db, uid, audio="https://cdn.test/full.mp3", preview="", seconds=None)
    finally:
        db.close()

    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audio_url"] is None, "片段缺失时必须 fail-closed，不得回退完整音频 URL"
    assert body["can_play_full"] is False


# ---------- 5. 下载门控 ----------
def test_download_requires_subscription(client, tmp_path, monkeypatch):
    email = "gate-dl@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db, uid, audio="https://cdn.test/full.mp3", preview="https://cdn.test/preview.mp3"
        )
    finally:
        db.close()

    # 未订阅 → 403
    r = client.get(f"/api/songs/{song.id}/download", headers=h)
    assert r.status_code == 403, r.text

    # 订阅后 → 200（把 S3 下载替换成本地临时文件，避免真实网络）
    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"ID3\x03" + b"\x00" * 512)
    monkeypatch.setattr("app.storage.s3.download_to_temp", lambda url, **kw: str(fake))

    r = client.post("/api/auth/subscribe", json={"tier_key": "sacred"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/songs/{song.id}/download", headers=h)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")


# ---------- 6. ffmpeg 裁剪：确实变短 ----------
def test_trim_shortens_audio(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 不可用（试听片段生成依赖系统 ffmpeg）")

    sr = 8000
    src = tmp_path / "in.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * t / sr)))
                for t in range(sr * 3)  # 3 秒
            )
        )

    dst = tmp_path / "out.wav"
    postprocess.trim(str(src), str(dst), 1)
    assert dst.exists() and dst.stat().st_size > 0

    with wave.open(str(dst)) as w:
        dur = w.getnframes() / w.getframerate()
    assert 0.8 <= dur <= 1.4, f"裁剪后时长应约 1 秒，实际 {dur:.2f}s"


# ---------- 7~9. 无对象存储时的「按需生成」回退 ----------
# 배경: storage_* 미설정 환경에서 _make_preview 가 그냥 실패하면 미구독자는
#       audio_url=None 이 되어 "아무 소리도 못 듣는" 기능정지가 된다.
#       → 로컬 캐시 + GET /api/songs/{id}/preview 폴백이 반드시 있어야 한다.
def _make_wav(path, seconds: int, sr: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * t / sr)))
                for t in range(sr * seconds)
            )
        )


def _use_tmp_cache(monkeypatch, tmp_path) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "preview_cache_dir", str(tmp_path / "pcache"), raising=False)


def test_make_preview_falls_back_to_local_endpoint(client, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 不可用（试听片段生成依赖系统 ffmpeg）")

    from app.config import settings
    from app.routers.songs import _make_preview

    _use_tmp_cache(monkeypatch, tmp_path)
    assert settings.storage_bucket == "", "本用例假设对象存储未配置（回退路径）"

    h = _register(client, "gate-fallback@test.com")
    db = SessionLocal()
    try:
        uid = _uid(db, "gate-fallback@test.com")
        song = _mk_song(db, uid, audio="https://cdn.test/full.mp3", preview="", seconds=None)
    finally:
        db.close()

    src = tmp_path / "full.wav"
    _make_wav(src, 3)
    monkeypatch.setattr("app.storage.s3.download_to_temp", lambda url, **kw: str(src))

    url = _make_preview(song.audio_url, song.id)
    assert url == f"/api/songs/{song.id}/preview", f"无对象存储时应回退到本地端点，实际 {url!r}"

    r = client.get(f"/api/songs/{song.id}/preview", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/")
    assert len(r.content) > 0, "试听片段内容不应为空"


def test_preview_endpoint_never_serves_full_audio(client, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 不可用（试听片段生成依赖系统 ffmpeg）")

    from app.config import settings

    _use_tmp_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "preview_seconds", 1, raising=False)

    h = _register(client, "gate-nofull@test.com")
    db = SessionLocal()
    try:
        uid = _uid(db, "gate-nofull@test.com")
        song = _mk_song(db, uid, audio="https://cdn.test/full.mp3", preview="", seconds=None)
    finally:
        db.close()

    src = tmp_path / "full30.wav"
    _make_wav(src, 30)
    monkeypatch.setattr("app.storage.s3.download_to_temp", lambda url, **kw: str(src))

    r = client.get(f"/api/songs/{song.id}/preview", headers=h)
    assert r.status_code == 200, r.text
    # 30 秒源 → 只应下发约 1 秒；若下发完整音频体积会大一个量级
    assert len(r.content) < src.stat().st_size / 5, (
        f"试听端点下发了过大内容（疑似完整音频泄漏）：{len(r.content)}B vs 源 {src.stat().st_size}B"
    )


# ---------- 10~11. 上游慢/过大时的护栏（按需生成是用户可见同步路径） ----------
class _SlowResp:
    """천천히 but 꾸준히 흐르는 업스트림 — httpx read timeout 으로는 잡히지 않는다."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, _n):
        def gen():
            while True:
                time.sleep(0.02)
                yield b"x" * 4096

        return gen()


class _FakeGuard:
    """SSRF 가드를 통과시키고, 업스트림은 _SlowResp 로 대체하는 페이크 가드.

    H4(2026-09-13) 이후 download_to_temp 는 ``ssrf_guard.guarded_get_stream``(컨텍스트
    매니저) 를 통해서만 내려받으므로, 예전처럼 ``s3.httpx.stream`` 을 패치하면
    아무 효과가 없다(실제 네트워크 호출로 빠져나간다).
    """

    def guard(self, url):
        return url

    @contextlib.contextmanager
    def guarded_get_stream(self, url, timeout=30, max_hops=5):
        with _SlowResp() as resp:
            yield resp


def test_download_guard_total_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr("app.storage.s3.ssrf_guard", _FakeGuard())

    from app.storage.s3 import download_to_temp

    pattern = os.path.join(tempfile.gettempdir(), "tmp*.mp3")
    before = set(glob.glob(pattern))
    with pytest.raises(TimeoutError):
        download_to_temp("https://cdn.test/slow.mp3", timeout_total=0.3, max_bytes=50 * 1024 * 1024)
    # 실패 시 임시 파일이 남으면 디스크 누수 → 반드시 정리돼야 한다
    leaked = set(glob.glob(pattern)) - before
    assert not leaked, f"下载失败后残留临时文件：{leaked}"


def test_download_guard_max_bytes(monkeypatch):
    monkeypatch.setattr("app.storage.s3.ssrf_guard", _FakeGuard())

    from app.storage.s3 import download_to_temp

    with pytest.raises(ValueError):
        download_to_temp("https://cdn.test/big.mp3", timeout_total=10, max_bytes=64 * 1024)


def test_preview_endpoint_fails_closed_on_slow_upstream(client, tmp_path, monkeypatch):
    """上游卡住时：不得挂起，也不得下发完整音频 → 404。"""
    _use_tmp_cache(monkeypatch, tmp_path)
    from app.config import settings

    monkeypatch.setattr(settings, "preview_fetch_timeout", 0.2, raising=False)
    monkeypatch.setattr("app.storage.s3.ssrf_guard", _FakeGuard())

    h = _register(client, "gate-slow@test.com")
    db = SessionLocal()
    try:
        uid = _uid(db, "gate-slow@test.com")
        song = _mk_song(db, uid, audio="https://cdn.test/full.mp3", preview="", seconds=None)
    finally:
        db.close()

    r = client.get(f"/api/songs/{song.id}/preview", headers=h)
    assert r.status_code == 404, f"上游不可靠时应 fail-closed(404)，实际 {r.status_code}"


def test_preview_endpoint_requires_ownership(client, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 不可用（试听片段生成依赖系统 ffmpeg）")

    _use_tmp_cache(monkeypatch, tmp_path)
    h_owner = _register(client, "gate-owner@test.com")
    h_other = _register(client, "gate-stranger@test.com")

    db = SessionLocal()
    try:
        uid = _uid(db, "gate-owner@test.com")
        song = _mk_song(
            db, uid, audio="https://cdn.test/full.mp3", preview="", seconds=None, is_public=False
        )
    finally:
        db.close()

    # 私有曲：非所有者 404（且不得泄露存在性）
    r = client.get(f"/api/songs/{song.id}/preview", headers=h_other)
    assert r.status_code == 404, r.text

    # 公开后他人可试听（发现页对所有人开放 60 秒试听，与本规则一致）
    src = tmp_path / "pub.wav"
    _make_wav(src, 2)
    monkeypatch.setattr("app.storage.s3.download_to_temp", lambda url, **kw: str(src))

    db = SessionLocal()
    try:
        s = db.get(Song, song.id)
        s.is_public = True
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/songs/{song.id}/preview", headers=h_other)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/songs/{song.id}/preview", headers=h_owner)
    assert r.status_code == 200, r.text
