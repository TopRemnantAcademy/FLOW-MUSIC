"""P3 音频机审测试：生成完成后对最终音频做内容审核的状态机协同。

后端全 mock（MockProvider 返回非空 audio_url），不触达真实 ASR/审核 API。
沿用 test_explore_play.py 的 `_drive` 显式驱动约定。
"""

import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.models import Song
from app.routers.songs import _process_song


def _drive(song_id: int) -> None:
    """显式驱动后台处理函数（与生产 BackgroundTasks 调用的是同一函数）。"""
    asyncio.run(_process_song(song_id))


def _register(client: TestClient, email: str = "audio@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_audio_moderation_rejects_on_failure(client, monkeypatch):
    """启用音频审核且未通过 → moderation_status='rejected'，不进 explore。"""
    import app.moderation as moderation_mod

    monkeypatch.setattr(settings, "enable_audio_moderation", True)
    monkeypatch.setattr(moderation_mod, "moderate_audio", lambda audio_url: False)

    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)

    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "rejected"

    ids = {x["id"] for x in client.get("/api/songs/explore").json()}
    assert sid not in ids


def test_audio_moderation_disabled_keeps_auto_approve(client, monkeypatch):
    """未启用音频审核 → 行为与 E4 一致，自动 approved 并进 explore。"""
    monkeypatch.setattr(settings, "enable_audio_moderation", False)

    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)

    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "approved"

    ids = {x["id"] for x in client.get("/api/songs/explore").json()}
    assert sid in ids


def test_audio_moderation_does_not_override_admin_approved(client, monkeypatch):
    """admin 已 approved 的歌，即使音频审核未通过也不覆盖（仍为 approved）。"""
    import app.moderation as moderation_mod

    monkeypatch.setattr(settings, "enable_audio_moderation", True)
    monkeypatch.setattr(moderation_mod, "moderate_audio", lambda audio_url: False)

    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    # 模拟 admin 已审核通过
    db = SessionLocal()
    db.get(Song, sid).moderation_status = "approved"
    db.commit()
    db.close()
    _drive(sid)

    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "approved"
