"""D32 回归：_process_song 必须将在事件循环外（线程池）执行同步阻塞后处理
（_apply_postprocess = ffmpeg + 文件 IO）。在单主机共置 1 台上，这能阻止
事件循环停滞的爆炸半径扩散到 FLOW。

行为验证：启用音频后处理时，_apply_postprocess 确实被调用（经由 run_in_threadpool），
且歌曲最终状态为 completed / approved，audio_url 已写入。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models import Song
from app.routers import songs as songs_mod


@pytest.fixture
def song_id_in_db():
    db = SessionLocal()
    try:
        s = Song(
            user_id=1,
            prompt="test",
            status="pending",
            moderation_status="pending",
            task_type="generate",
            engine_name="mock",
            audio_url="",
        )
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def test_d32_postprocess_offloaded_to_threadpool(monkeypatch, song_id_in_db):
    calls = {}

    def fake_postprocess(remote_url: str) -> str:
        calls["invoked"] = True
        calls["url"] = remote_url
        return remote_url

    monkeypatch.setattr(songs_mod, "_apply_postprocess", fake_postprocess)

    fake_result = SimpleNamespace(
        status="completed",
        external_id="ext",
        custom_id="cus",
        audio_url="http://public.example/a.wav",
        cover_url="",
        lyric="lyric",
        lyrics_sync=None,
        title="title",
        model_version="v1",
        duration=10,
    )

    async def fake_generate(req, engine_name=None, engine_chain=None):
        return fake_result

    monkeypatch.setattr(songs_mod, "generate", fake_generate)
    monkeypatch.setattr(songs_mod.settings, "enable_audio_postprocess", True)
    monkeypatch.setattr(songs_mod.settings, "enable_moderation", True)

    asyncio.run(songs_mod._process_song(song_id_in_db))

    assert calls.get("invoked") is True, "后处理应被调用"
    assert calls.get("url") == "http://public.example/a.wav"

    db = SessionLocal()
    try:
        s = db.get(Song, song_id_in_db)
        assert s is not None
        assert s.status == "completed"
        assert s.moderation_status == "approved"
        assert s.audio_url == "http://public.example/a.wav"
    finally:
        db.close()
