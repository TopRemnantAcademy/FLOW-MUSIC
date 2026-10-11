"""P1 后期处理端点测试：对齐 open.suno.cn 真实端点（whole-song/crop/speed/aligned-lyrics/upload/sound）。

用 conftest 注入的 MockProvider（无真实 Suno 调用），验证：
- 后台任务 _process_post / _process_sound 正确写回 Song
- 6 个端点均受理（200 + 返回 song_id / custom_id）
不臆造任何 Suno 字段，断言仅依赖 MockProvider 契约。
"""

import asyncio

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Song, User
from app.schemas import SoundReq
from app.routers.songs import _process_post, _process_sound

PP_AUDIO = "https://mock.cdn.example.com/pp.mp3"


def _auth_headers(client: TestClient, email: str) -> dict:
    pw = "Password123!"
    client.post("/api/auth/register", json={"email": email, "password": pw})
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_song(email: str, custom_id: str = "mock-custom-0001") -> int:
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    s = Song(
        user_id=u.id,
        title="t",
        status="completed",
        custom_id=custom_id,
        audio_url="orig.mp3",
        lyric="orig lyric",
        task_type="generate",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    db.close()
    return s.id


def _song(sid: int) -> Song:
    db = SessionLocal()
    s = db.get(Song, sid)
    db.close()
    return s


def test_crop_writes_back_audio(client):
    email = "crop@test.com"
    _auth_headers(client, email)
    sid = _make_song(email)
    asyncio.run(_process_post(sid, "crop", clip_id="x", start_time=0, end_time=10))
    assert _song(sid).audio_url == PP_AUDIO


def test_speed_and_whole_song_write_back(client):
    for op, params in [
        ("speed", {"clip_id": "x", "speed": 1.5}),
        ("whole_song", {"clip_id": "x"}),
    ]:
        email = f"{op}@test.com"
        _auth_headers(client, email)
        sid = _make_song(email)
        asyncio.run(_process_post(sid, op, **params))
        assert _song(sid).audio_url == PP_AUDIO


def test_aligned_lyrics_writes_back(client):
    email = "align@test.com"
    _auth_headers(client, email)
    sid = _make_song(email)
    asyncio.run(_process_post(sid, "aligned_lyrics", lyrics="hello", suno_id="x"))
    assert "[00:00]" in _song(sid).lyric


def test_crop_endpoint_accepted(client):
    email = "crope@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/crop", json={"start_time": 0, "end_time": 10}, headers=h)
    assert r.status_code == 200
    assert r.json()["song_id"] == sid


def test_speed_endpoint_accepted(client):
    email = "speede@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/speed", json={"speed": 1.5}, headers=h)
    assert r.status_code == 200


def test_whole_song_endpoint_accepted(client):
    email = "whole@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/whole-song", headers=h)
    assert r.status_code == 200


def test_aligned_lyrics_endpoint_accepted(client):
    email = "aligne@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/aligned-lyrics", json={"lyrics": "hi"}, headers=h)
    assert r.status_code == 200


def test_sound_endpoint_and_writeback(client):
    email = "sound@test.com"
    h = _auth_headers(client, email)
    r = client.post("/api/songs/sound", json={"title": "sfx", "tags": "rain"}, headers=h)
    assert r.status_code == 200
    sid = r.json()["song_id"]
    asyncio.run(_process_sound(sid, SoundReq(title="sfx", tags="rain")))
    s = _song(sid)
    assert s.audio_url == PP_AUDIO
    assert s.task_type == "sound"


def test_upload_returns_custom_id(client):
    email = "upload@test.com"
    h = _auth_headers(client, email)
    r = client.post("/api/songs/upload", json={"audio_url": "https://x.com/a.mp3"}, headers=h)
    assert r.status_code == 200
    assert r.json()["custom_id"] == "mock-custom-upload-1"
