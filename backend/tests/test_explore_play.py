"""P4 内容公开与审核闭环测试：探索页过滤、播放计数、生成后自动审批(E4)。

后端全 mock，不触达真实 Suno/S3。沿用 test_api.py 的 `_drive` 显式驱动约定。
"""

import asyncio

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Song
from app.routers.songs import _process_song


def _drive(song_id: int) -> None:
    """显式驱动后台处理函数（与生产 BackgroundTasks 调用的是同一函数）。"""
    asyncio.run(_process_song(song_id))


def _register(client: TestClient, email: str = "explore@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_explore_returns_only_public_approved(client):
    h = _register(client)
    # 公开 + 生成完成后应自动 approved → 进入探索页
    r1 = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid1 = r1.json()["song_id"]
    _drive(sid1)
    # 私有歌 → 不进入探索页
    r2 = client.post("/api/songs/generate", json={"prompt": "y", "is_public": False}, headers=h)
    sid2 = r2.json()["song_id"]
    _drive(sid2)

    res = client.get("/api/songs/explore")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()}
    assert sid1 in ids
    assert sid2 not in ids

    # 公开歌被 admin 驳回（置 pending）后退出探索页
    db = SessionLocal()
    db.get(Song, sid1).moderation_status = "pending"
    db.commit()
    db.close()
    ids2 = {s["id"] for s in client.get("/api/songs/explore").json()}
    assert sid1 not in ids2
    assert sid2 not in ids2


def test_play_increments_play_count(client):
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)

    res = client.post(f"/api/songs/{sid}/play", headers=h)
    assert res.status_code == 200
    assert res.json()["play_count"] == 1
    res2 = client.post(f"/api/songs/{sid}/play", headers=h)
    assert res2.json()["play_count"] == 2


def test_generate_auto_approves_after_completion(client):
    """E4：提交文本已机审，生成完成后自动置 moderation_status='approved'。"""
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "x"}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["status"] == "completed"
    assert s["moderation_status"] == "approved"
