"""GAP-010 音频格式/质量选择测试。

实证前提（2026-10-11 逐字核对 platform.mureka.ai OpenAPI spec）：
Mureka `POST /v1/song/generate` 请求体**没有** quality/format/bitrate/duration 入参
（全站 spec "bitrate"/"quality" 出现 0 次），但响应 choices[] 同时回传
url(mp3)/flac_url/wav_url。故本特性 = **仅下载侧格式可选**：
- 上游三链接落库（audio_url / audio_wav_url / audio_flac_url）；
- 下载端点按 ?format=mp3|wav|flac 路由到对应链接；
- 无损缺失时不静默降级（409），由 available_formats 让前端预判。

钉住的契约：
1. 无格式入参不臆造——不会出现 quality_kbps 之类的假字段（模型层断言）。
2. 下载端点三格式路由到各自 URL。
3. 无损缺失 → 409（不静默丢 mp3）。
4. 非法格式 → 400。
5. 未订阅 → 403（格式选项不绕过付费门）。
6. SongOut.available_formats 如实反映可用格式。
"""

import httpx
import pytest

from app.db import SessionLocal
from app.models import Song, User
from app.orchestrator.base import GenerateRequest, SongResult
from app.orchestrator.mureka import MurekaProvider


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
        title=kw.pop("title", "格式测试曲"),
        status="completed",
        audio_url=kw.pop("audio_url", "https://cdn.test/song.mp3"),
        moderation_status="approved",
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _subscribe(client, h, tier="standard"):
    r = client.post("/api/auth/subscribe", json={"tier_key": tier}, headers=h)
    assert r.status_code == 200, r.text


# ---------- 1. 模型层：拒绝臆造 quality/bitrate 字段 ----------
def test_model_has_no_fabricated_quality_column():
    """Mureka 入参无码率控制 → 不得存在 quality_kbps 之类「存了也发不出」的列。"""
    cols = {c.name for c in Song.__table__.columns}
    assert "audio_format" in cols
    assert "audio_wav_url" in cols
    assert "audio_flac_url" in cols
    assert not any("kbps" in c or "bitrate" in c or "quality" in c for c in cols), (
        f"出现臆造质量列：{sorted(cols)}"
    )


# ---------- 2. Mureka 响应解析：无损链接落 SongResult ----------
def test_mureka_parses_lossless_urls():
    p = MurekaProvider.__new__(MurekaProvider)
    p.model = "mureka-9"
    body = {
        "status": "succeeded",
        "model": "mureka-9",
        "choices": [
            {
                "id": "c1",
                "url": "https://cdn.test/a.mp3",
                "flac_url": "https://cdn.test/a.flac",
                "wav_url": "https://cdn.test/a.wav",
                "duration": 156710,
            }
        ],
    }
    res = p._parse_task_body(body, "task-1")
    assert res.status == "completed"
    assert res.audio_url == "https://cdn.test/a.mp3"
    assert res.flac_url == "https://cdn.test/a.flac"
    assert res.wav_url == "https://cdn.test/a.wav"


def test_mureka_missing_lossless_urls_are_none():
    """老/异常响应无 flac_url/wav_url → None（不编造占位链接）。"""
    p = MurekaProvider.__new__(MurekaProvider)
    p.model = "mureka-9"
    body = {"status": "succeeded", "choices": [{"id": "c1", "url": "https://cdn.test/a.mp3"}]}
    res = p._parse_task_body(body, "task-2")
    assert res.audio_url == "https://cdn.test/a.mp3"
    assert res.wav_url is None
    assert res.flac_url is None


def test_generate_payload_carries_no_format_param():
    """生成请求体不得夹带 format/quality/bitrate —— 上游不支持，带了即臆造。"""
    import asyncio

    p = MurekaProvider.__new__(MurekaProvider)
    p.base = "https://api.mureka.ai/v1"
    p.api_key = "k"
    p.model = "mureka-9"
    p.n = 1
    p._sem = asyncio.Semaphore(1)
    p._timeout = None
    captured = {}

    async def fake_post(path, json):
        captured["json"] = json
        return {"id": "1", "status": "preparing", "model": "mureka-9"}

    p._post = fake_post
    asyncio.run(p.create(GenerateRequest(lyric="x", style="piano")))
    for bad in ("format", "quality", "bitrate", "kbps", "audio_format"):
        assert bad not in captured["json"], f"请求体不应包含 {bad}：{captured['json']}"


# ---------- 3. 下载端点：三格式路由 ----------
def _fake_local(tmp_path, name: str, content: bytes = b"ID3\x03" + b"\x00" * 64):
    f = tmp_path / name
    f.write_bytes(content)
    return f


def test_download_routes_each_format_to_its_url(client, tmp_path, monkeypatch):
    email = "fmt-dl@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db,
            uid,
            audio_url="https://cdn.test/a.mp3",
            audio_wav_url="https://cdn.test/a.wav",
            audio_flac_url="https://cdn.test/a.flac",
        )
    finally:
        db.close()
    _subscribe(client, h)

    seen = {}

    def fake_dl(url, **kw):
        seen["url"] = url
        ext = url.rsplit(".", 1)[-1]
        return str(_fake_local(tmp_path, f"out.{ext}"))

    monkeypatch.setattr("app.storage.s3.download_to_temp", fake_dl)

    for fmt, expect_url, expect_media in (
        ("mp3", "https://cdn.test/a.mp3", "audio/mpeg"),
        ("wav", "https://cdn.test/a.wav", "audio/wav"),
        ("flac", "https://cdn.test/a.flac", "audio/flac"),
    ):
        r = client.get(f"/api/songs/{song.id}/download?format={fmt}", headers=h)
        assert r.status_code == 200, (fmt, r.text)
        assert seen["url"] == expect_url
        assert r.headers["content-type"].startswith(expect_media)
        assert f".{fmt}" in r.headers.get("content-disposition", "")


def test_download_defaults_to_stored_format(client, tmp_path, monkeypatch):
    """未传 format → 用曲目 audio_format 字段（默认 mp3，保持既有行为）。"""
    email = "fmt-default@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db, uid, audio_url="https://cdn.test/a.mp3", audio_flac_url="https://cdn.test/a.flac"
        )
    finally:
        db.close()
    _subscribe(client, h)
    seen = {}

    def fake_dl(url, **kw):
        seen["url"] = url
        return str(_fake_local(tmp_path, "out.mp3"))

    monkeypatch.setattr("app.storage.s3.download_to_temp", fake_dl)
    r = client.get(f"/api/songs/{song.id}/download", headers=h)
    assert r.status_code == 200, r.text
    assert seen["url"] == "https://cdn.test/a.mp3"


# ---------- 4. 无损缺失 → 409（不静默降级） ----------
def test_download_missing_lossless_is_409_not_silent_downgrade(client, tmp_path, monkeypatch):
    email = "fmt-missing@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        # 无 wav_url / flac_url（老数据或上游未返）
        song = _mk_song(db, uid, audio_url="https://cdn.test/a.mp3")
    finally:
        db.close()
    _subscribe(client, h)

    called = {"n": 0}

    def fake_dl(url, **kw):
        called["n"] += 1
        return str(_fake_local(tmp_path, "out.mp3"))

    monkeypatch.setattr("app.storage.s3.download_to_temp", fake_dl)

    for fmt in ("wav", "flac"):
        r = client.get(f"/api/songs/{song.id}/download?format={fmt}", headers=h)
        assert r.status_code == 409, (fmt, r.text)
        assert fmt.upper() in r.json()["detail"]
    # 关键：不得因为缺无损就偷偷回源 mp3
    assert called["n"] == 0, "缺失格式时必须 409，不得静默返回 mp3"


# ---------- 5. 非法格式 → 400 ----------
def test_download_rejects_unknown_format(client, tmp_path, monkeypatch):
    email = "fmt-bad@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(db, uid)
    finally:
        db.close()
    _subscribe(client, h)
    monkeypatch.setattr(
        "app.storage.s3.download_to_temp", lambda url, **kw: str(_fake_local(tmp_path, "o.mp3"))
    )
    r = client.get(f"/api/songs/{song.id}/download?format=ogg", headers=h)
    assert r.status_code == 400, r.text


# ---------- 6. 未订阅 → 403（格式不绕过付费门） ----------
def test_download_format_still_requires_subscription(client, tmp_path, monkeypatch):
    email = "fmt-free@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        song = _mk_song(
            db, uid, audio_url="https://cdn.test/a.mp3", audio_flac_url="https://cdn.test/a.flac"
        )
    finally:
        db.close()
    monkeypatch.setattr(
        "app.storage.s3.download_to_temp", lambda url, **kw: str(_fake_local(tmp_path, "o.flac"))
    )
    r = client.get(f"/api/songs/{song.id}/download?format=flac", headers=h)
    assert r.status_code == 403, r.text


# ---------- 7. SongOut.available_formats 如实反映 ----------
def test_song_out_available_formats(client):
    email = "fmt-avail@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        with_wav = _mk_song(
            db,
            uid,
            title="有wav",
            audio_url="https://cdn.test/a.mp3",
            audio_wav_url="https://cdn.test/a.wav",
        )
        only_mp3 = _mk_song(db, uid, title="只mp3", audio_url="https://cdn.test/b.mp3")
        wav_id, mp3_id = with_wav.id, only_mp3.id
    finally:
        db.close()

    b1 = client.get(f"/api/songs/{wav_id}", headers=h).json()
    assert b1["available_formats"] == ["mp3", "wav"]
    assert b1["audio_format"] == "mp3"

    b2 = client.get(f"/api/songs/{mp3_id}", headers=h).json()
    assert b2["available_formats"] == ["mp3"]


# ---------- 8. 创建时格式偏好 + 权益联动 ----------
def _gen_payload(**kw):
    return {"task": "generate", "style": "piano", "lyric": "la la", "title": "t", **kw}


def test_create_defaults_to_mp3_without_format(client):
    """不传 audio_format → 落库 mp3（保持既有行为，不是无损）。"""
    email = "fmt-create-default@test.com"
    h = _register(client, email)
    r = client.post("/api/songs/generate", json=_gen_payload(), headers=h)
    assert r.status_code in (200, 201, 202), r.text
    sid = r.json().get("song_id") or r.json().get("id")
    db = SessionLocal()
    try:
        assert db.get(Song, sid).audio_format == "mp3"
    finally:
        db.close()


def test_create_rejects_free_user_lossless(client):
    """未订阅者显式请求 FLAC → 403（不静默存下、不等到下载才发现）。"""
    email = "fmt-create-free@test.com"
    h = _register(client, email)
    r = client.post("/api/songs/generate", json=_gen_payload(audio_format="flac"), headers=h)
    assert r.status_code == 403, r.text
    assert "订阅" in r.json()["detail"]


def test_create_rejects_unknown_format(client):
    email = "fmt-create-bad@test.com"
    h = _register(client, email)
    r = client.post("/api/songs/generate", json=_gen_payload(audio_format="ogg"), headers=h)
    assert r.status_code == 400, r.text


def test_create_accepts_subscriber_lossless_preference(client):
    email = "fmt-create-sub@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    r = client.post("/api/songs/generate", json=_gen_payload(audio_format="flac"), headers=h)
    assert r.status_code in (200, 201, 202), r.text
    sid = r.json().get("song_id") or r.json().get("id")
    db = SessionLocal()
    try:
        assert db.get(Song, sid).audio_format == "flac"
    finally:
        db.close()


# ---------- 9. #152 声明与下载同口径：available_formats ⊆ 可下载 ----------
def test_available_formats_follows_full_audio_url_fallback(client, monkeypatch):
    """#152：mp3 的可用性由 `full_audio_url` 决定，而不是「audio_url 列非空」。

    上游链接 30 天会过期，`full_audio_url` 才是「此刻拿得到」的唯一口径；
    两处各写一份 fallback → 前端渲染出选项、点下去 409（本 issue 的场景）。
    这里把 `full_audio_url` 打桩成「CDN 挂了」→ 必须自动从下拉框移除 mp3。
    """
    from app.routers import songs

    email = "fmt-152@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), title="152", audio_url="https://cdn.test/a.mp3")
        sid = song.id
    finally:
        db.close()

    assert client.get(f"/api/songs/{sid}", headers=h).json()["available_formats"] == ["mp3"]

    # CDN 失效 + 上游链也拿不到（如已过期被清理）→ 声明里不能再出现 mp3
    monkeypatch.setattr(songs, "full_audio_url", lambda s, **kw: "")
    body = client.get(f"/api/songs/{sid}", headers=h).json()
    assert body["available_formats"] == []
    # 声明与下载一致：此时下载也必须是 409（而不是「列表里没有却还下得到」）
    _subscribe(client, h)
    monkeypatch.setattr("app.storage.s3.download_to_temp", lambda url, **kw: "/tmp/x.mp3")
    r = client.get(f"/api/songs/{sid}/download?format=mp3", headers=h)
    assert r.status_code == 409, r.text


def test_every_available_format_is_downloadable_152(client, monkeypatch, tmp_path):
    """#152 的核心不变量：**列出的每个格式都真的下得到**（逐格式走一遍下载端点）。"""
    email = "fmt-152b@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(
            db,
            _uid(db, email),
            title="全格式",
            audio_url="https://cdn.test/a.mp3",
            audio_wav_url="https://cdn.test/a.wav",
            audio_flac_url="https://cdn.test/a.flac",
        )
        sid = song.id
    finally:
        db.close()

    listed = client.get(f"/api/songs/{sid}", headers=h).json()["available_formats"]
    assert listed == ["mp3", "wav", "flac"]
    monkeypatch.setattr(
        "app.storage.s3.download_to_temp", lambda url, **kw: str(tmp_path / "o.bin")
    )
    (tmp_path / "o.bin").write_bytes(b"\x00")
    for fmt in listed:
        r = client.get(f"/api/songs/{sid}/download?format={fmt}", headers=h)
        assert r.status_code == 200, f"{fmt} 声明可用却下不到：{r.status_code} {r.text}"


def test_available_formats_uses_cdn_when_present_152(client, monkeypatch):
    """有 CDN 链接时 mp3 仍可用（口径切换不能把正常曲目误判成不可下载）。"""
    from app.routers import songs

    email = "fmt-152c@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), title="cdn", audio_url="")
        song.audio_cdn_url = "https://bucket.test/songs/x/y.mp3"
        db.commit()
        sid = song.id
    finally:
        db.close()

    assert client.get(f"/api/songs/{sid}", headers=h).json()["available_formats"] == ["mp3"]
    # wav 缺失 → 仍不列出（不因 mp3 走 CDN 就放宽无损的判据）
    assert songs._download_source(object(), "mp3") == ""
