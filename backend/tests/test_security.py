"""C 系安全/可用性打磨测试：C5 注册校验、D1 列表分页/删除/可见性切换。

后端全 mock，不触达真实 Suno/S3。沿用 test_api.py 的 `_drive` 显式驱动约定。
"""

import asyncio

from fastapi.testclient import TestClient

from app.routers.songs import _process_song


def _drive(song_id: int) -> None:
    """显式驱动后台处理函数（与生产 BackgroundTasks 调用的是同一函数）。"""
    asyncio.run(_process_song(song_id))


def _register(client: TestClient, email: str = "sec@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_register_rejects_weak_password(client):
    r = client.post("/api/auth/register", json={"email": "weak@test.com", "password": "123"})
    assert r.status_code == 400


def test_list_songs_pagination(client):
    h = _register(client)
    for _ in range(3):
        r = client.post("/api/songs/generate", json={"prompt": "分页测试"}, headers=h)
        _drive(r.json()["song_id"])
    # limit=1 只取最新一首
    res = client.get("/api/songs?limit=1", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    # 按 id desc，最新的一首 id 最大
    all_ids = {s["id"] for s in client.get("/api/songs", headers=h).json()}
    assert data[0]["id"] == max(all_ids)


def test_delete_own_song(client):
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "删除测试"}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)
    res = client.delete(f"/api/songs/{sid}", headers=h)
    assert res.status_code == 200
    # 删除后不可见
    assert client.get(f"/api/songs/{sid}", headers=h).status_code == 404


def test_delete_other_user_song_forbidden(client):
    h1 = _register(client, "owner@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "他人歌"}, headers=h1)
    sid = r.json()["song_id"]
    _drive(sid)
    h2 = _register(client, "other@test.com")
    # 非所有者删除：按"不存在"语义返回 404（不泄露存在性）
    res = client.delete(f"/api/songs/{sid}", headers=h2)
    assert res.status_code == 404


def test_update_visibility_toggles_explore(client):
    h = _register(client)
    r = client.post(
        "/api/songs/generate", json={"prompt": "可见性测试", "is_public": False}, headers=h
    )
    sid = r.json()["song_id"]
    _drive(sid)  # 完成后自动 approved，但 is_public=False → 不进 explore
    assert client.get(f"/api/songs/{sid}", headers=h).json()["moderation_status"] == "approved"

    # 设为公开 → 进入 explore
    res = client.patch(f"/api/songs/{sid}", json={"is_public": True}, headers=h)
    assert res.status_code == 200 and res.json()["is_public"] is True
    ids = {s["id"] for s in client.get("/api/songs/explore").json()}
    assert sid in ids

    # 改回私有 → 退出 explore
    client.patch(f"/api/songs/{sid}", json={"is_public": False}, headers=h)
    ids2 = {s["id"] for s in client.get("/api/songs/explore").json()}
    assert sid not in ids2
