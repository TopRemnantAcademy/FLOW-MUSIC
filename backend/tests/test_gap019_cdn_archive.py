"""GAP-019 CDN / 对象存储：主播放链路优先 CDN + 归档策略。

钉住的契约（每条都对应一个**曾经真实存在**的失败模式）：
1. 主播放链路优先 CDN：`SongOut.audio_url` 在 `audio_cdn_url` 非空时给 CDN 链接
   （上游链接 30 天过期 → APK 上表现为「过几天就播不了」）。
2. 优雅降级是硬指标：未配 storage_* / audio_cdn_url 为 NULL → 回上游、**不 500**。
   prod 当前就是这条路径（.env 里 0 条 storage_*）。
3. 付费门不因换出口而失效：未订阅者拿到的仍是**片段**（fail-closed，片段缺失给 None）。
4. 回填幂等：对象键由 `cdn.song_key` 唯一决定；已有 CDN 链接不重复回源。
5. 下载 / 试听片段 / FLOW 同步都走 CDN 出口。
6. 归档元数据（archived_at / storage_class）与阈值配置可读，且拒绝 GLACIER 类
   （不支持直接 GET → 归档 = 点不了播）。

所有网络 IO 一律被 monkeypatch 掉：本文件**不打任何外部请求**。
"""

import asyncio
import datetime

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.storage import mirror
from app.storage import s3 as storage
from app.storage.cdn import (
    archive_after_days,
    archive_storage_class,
    media_type_for,
    song_key,
)
from app.storage.transport import full_audio_url, has_cdn

BUCKET_PUBLIC = "https://cdn.example.com"
#: 上游「易失」链接（模拟 30 天后会 404 的供应商地址）。
#: ⚠ 用真实可解析的公共域名（example.com），因为回源前会过 SSRF 守卫 ——
#: `*.test` 解析不出公网 IP，守卫按 fail-closed 拒掉，测出来的就不是 CDN 行为了。
UPSTREAM = "https://example.com/volatile/song.mp3"


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _subscribe(client, h, tier="standard"):
    r = client.post("/api/auth/subscribe", json={"tier_key": tier}, headers=h)
    assert r.status_code == 200, r.text


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


def _mk_song(db, user_id: int, **kw) -> Song:
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "CDN 测试曲"),
        status="completed",
        audio_url=kw.pop("audio_url", UPSTREAM),
        moderation_status="approved",
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def storage_configured(monkeypatch):
    """把 storage_* 配全（但不建任何真实连接）。"""
    monkeypatch.setattr(settings, "storage_endpoint", "https://cos.test")
    monkeypatch.setattr(settings, "storage_bucket", "music-bucket")
    monkeypatch.setattr(settings, "storage_access_key", "ak")
    monkeypatch.setattr(settings, "storage_secret_key", "sk")
    monkeypatch.setattr(settings, "storage_public_base", BUCKET_PUBLIC)
    assert storage.is_configured() is True
    yield
    assert storage.is_configured() is True


@pytest.fixture
def no_storage(monkeypatch):
    """prod 现状：storage_* 全空 → 必须优雅降级。"""
    for attr in (
        "storage_endpoint",
        "storage_bucket",
        "storage_access_key",
        "storage_secret_key",
        "storage_public_base",
    ):
        monkeypatch.setattr(settings, attr, "")
    assert storage.is_configured() is False
    yield


# ═══════════════════════════════════════════════════════════════════════════
# 1. 对象键 / 媒体类型 / 存储类别：单一约定
# ═══════════════════════════════════════════════════════════════════════════
def test_song_key_is_stable_and_derived_from_source_ext():
    """键由 (user_id, song_id, 源扩展名) 唯一决定 —— 幂等回填的前提。"""
    assert song_key(7, 42, UPSTREAM) == "songs/7/42.mp3"
    # 带 query 的签名 URL 不能把 key 带歪（否则每次回填都换键 → 桶里堆副本）
    assert song_key(7, 42, "https://x.test/a.mp3?X-Amz-Signature=deadbeef") == "songs/7/42.mp3"
    assert song_key(7, 42, "https://x.test/a.flac") == "songs/7/42.flac"
    # 未知/无扩展名 → 兜底 mp3（不产生 .bin 那种没人认识的对象）
    assert song_key(7, 42, "https://x.test/stream") == "songs/7/42.mp3"
    assert song_key(None, 42, "") == "songs/0/42.mp3"


def test_media_type_matches_extension():
    assert media_type_for("https://x/a.mp3") == "audio/mpeg"
    assert media_type_for("https://x/a.flac?t=1") == "audio/flac"
    assert media_type_for("https://x/a.wav") == "audio/wav"
    assert media_type_for("https://x/weird") == "audio/mpeg"


def test_archive_config_defaults_are_safe(monkeypatch):
    monkeypatch.setattr(settings, "storage_archive_after_days", 90)
    monkeypatch.setattr(settings, "storage_class_archive", "")
    assert archive_after_days() == 90
    # 空 = 用 STANDARD_IA（绝不用 GLACIER：不支持直接 GET，会把歌变成「点了播不了」）
    assert archive_storage_class() == "STANDARD_IA"
    monkeypatch.setattr(settings, "storage_archive_after_days", "not-a-number")
    assert archive_after_days() == 0  # 脏配置 → 关闭归档，而不是崩


# ═══════════════════════════════════════════════════════════════════════════
# 2. 出口选择：CDN 优先、上游兜底
# ═══════════════════════════════════════════════════════════════════════════
def test_full_audio_url_prefers_cdn():
    class S:
        audio_url = UPSTREAM
        audio_cdn_url = f"{BUCKET_PUBLIC}/songs/1/2.mp3"

    assert full_audio_url(S()) == f"{BUCKET_PUBLIC}/songs/1/2.mp3"
    assert full_audio_url(S(), force_upstream=True) == UPSTREAM  # 需要原件时（分轨/后处理）


def test_full_audio_url_falls_back_to_upstream():
    class S:
        audio_url = UPSTREAM
        audio_cdn_url = ""  # NULL/空 → 降级

    assert full_audio_url(S()) == UPSTREAM
    assert has_cdn(S()) is False


def test_song_out_prefers_cdn_for_subscriber(client, storage_configured):
    """① 主播放链路：订阅者拿到的整曲链接是 CDN，不是上游。"""
    email = "cdn-sub@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(
            db, _uid(db, email), audio_cdn_url=f"{BUCKET_PUBLIC}/songs/9/9.mp3"
        )
    finally:
        db.close()
    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audio_url"] == f"{BUCKET_PUBLIC}/songs/9/9.mp3"
    assert UPSTREAM not in body["audio_url"]


def test_song_out_falls_back_to_upstream_when_no_cdn(client, storage_configured):
    """② audio_cdn_url 为 NULL → 回上游，且**绝不 500**。"""
    email = "cdn-null@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), audio_cdn_url="")
    finally:
        db.close()
    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["audio_url"] == UPSTREAM


def test_song_out_degrades_without_storage_configured(client, no_storage):
    """③ prod 现状（未配 storage_*）：直连上游、零回归、零 500。"""
    email = "cdn-nostorage@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email))
    finally:
        db.close()
    r = client.get(f"/api/songs/{song.id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["audio_url"] == UPSTREAM


def test_premium_gate_survives_cdn_switch(client, storage_configured):
    """④ 换出口不换门：未订阅者仍只拿片段；片段缺失时 fail-closed（给 None）。"""
    email = "cdn-gate@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        uid = _uid(db, email)
        with_preview = _mk_song(
            db,
            uid,
            audio_cdn_url=f"{BUCKET_PUBLIC}/songs/1/1.mp3",
            preview_audio_url=f"{BUCKET_PUBLIC}/preview/1.mp3",
        )
        without_preview = _mk_song(
            db, uid, audio_cdn_url=f"{BUCKET_PUBLIC}/songs/2/2.mp3", preview_audio_url=""
        )
        # 先取 id：session 关闭后 ORM 实例会 detached（属性过期读不了）
        with_id, without_id = with_preview.id, without_preview.id
    finally:
        db.close()

    body = client.get(f"/api/songs/{with_id}", headers=h).json()
    assert body["audio_url"] == f"{BUCKET_PUBLIC}/preview/1.mp3"
    assert body["can_play_full"] is False
    # 片段缺失 → None（**不能**回退整曲 CDN 链接，否则门控失效）
    body2 = client.get(f"/api/songs/{without_id}", headers=h).json()
    assert body2["audio_url"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. 回源（mirror）：幂等 / 降级 / 不重复
# ═══════════════════════════════════════════════════════════════════════════
def test_mirror_skips_when_not_configured(no_storage):
    """未配 storage → 只返回 None，不抛、不打网络。"""
    db = SessionLocal()
    try:
        song = _mk_song(db, 999001)
        out = asyncio.run(mirror.mirror_song_audio(song, db))
        assert out is None
        assert (song.audio_cdn_url or "") == ""
    finally:
        db.close()


def test_mirror_skips_when_no_source_url(storage_configured):
    db = SessionLocal()
    try:
        song = _mk_song(db, 999002, audio_url="")
        assert asyncio.run(mirror.mirror_song_audio(song, db)) is None
    finally:
        db.close()


def test_mirror_is_idempotent_and_uploads_once(storage_configured, monkeypatch):
    """已有 CDN 链接 → 不再下载/上传（回填脚本反复跑不会重复打桶）。"""
    calls = {"fetch": 0, "upload": 0}

    def fake_fetch(src, key, content_type, storage_class, song_ref):
        calls["fetch"] += 1
        return f"{BUCKET_PUBLIC}/{key}"

    monkeypatch.setattr(mirror, "_fetch_and_upload", fake_fetch)

    db = SessionLocal()
    try:
        song = _mk_song(db, 999003)
        sid = song.id
        first = asyncio.run(mirror.mirror_song_audio(song, db))
        assert first == f"{BUCKET_PUBLIC}/songs/999003/{sid}.mp3"
        second = asyncio.run(mirror.mirror_song_audio(song, db))
        assert second == first
        assert calls["fetch"] == 1, "已有 CDN 链接时不得再次回源"
    finally:
        db.close()


def test_mirror_failure_is_swallowed(storage_configured, monkeypatch):
    """回源彻底失败 → 返回 None（调用方降级），绝不向调用方抛异常。"""
    monkeypatch.setattr(
        mirror, "_fetch_and_upload", lambda *a, **kw: None
    )
    db = SessionLocal()
    try:
        song = _mk_song(db, 999004)
        assert asyncio.run(mirror.mirror_song_audio(song, db)) is None
    finally:
        db.close()


def test_mirror_blocks_ssrf_targets(storage_configured, monkeypatch):
    """[SEC] 上游链接指向内网/云元数据 → 回源前被 SSRF 守卫拦下。"""
    called = {"upload": 0}
    monkeypatch.setattr(
        mirror, "_fetch_and_upload", lambda *a, **kw: called.__setitem__("upload", 1)
    )
    db = SessionLocal()
    try:
        song = _mk_song(db, 999005, audio_url="http://169.254.169.254/latest/meta-data/")
        assert asyncio.run(mirror.mirror_song_audio(song, db)) is None
        assert called["upload"] == 0, "SSRF 目标不得进入下载上传路径"
    finally:
        db.close()


def test_fetch_and_upload_retries_with_backoff(storage_configured, monkeypatch):
    """瞬时失败要**真的重试**（旧实现把 sleep 写进 if 分支 → 重试等于连打三次）。"""
    monkeypatch.setattr(mirror, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mirror, "BACKOFF_BASE", 0.0)
    attempts = {"n": 0}
    sleeps = []
    monkeypatch.setattr(mirror.time, "sleep", lambda s: sleeps.append(s))

    def flaky(src, max_bytes):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return "/tmp/fake.mp3"

    monkeypatch.setattr(mirror, "_download_with_cap", flaky)
    monkeypatch.setattr(storage, "upload_file", lambda p, k, **kw: f"{BUCKET_PUBLIC}/{k}")
    out = mirror._fetch_and_upload(UPSTREAM, "songs/1/1.mp3", "audio/mpeg", "", 1)
    assert out == f"{BUCKET_PUBLIC}/songs/1/1.mp3"
    assert attempts["n"] == 3
    assert sleeps == [0.0, 0.0], "每次重试前都要退避（不是只在成功前）"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 生成完成 → 回源落库（存储已配置时）
# ═══════════════════════════════════════════════════════════════════════════
def test_generate_mirrors_audio_to_cdn(client, storage_configured, monkeypatch):
    """新生成曲：收尾即回源 → 首个详情响应就是 CDN 链接（本 GAP 的验收口径）。"""
    from app.orchestrator.client import get_orchestrator
    from app.routers.songs import _process_song

    uploaded = {}

    def fake_fetch(src, key, content_type, storage_class, song_ref):
        uploaded["src"] = src
        uploaded["key"] = key
        uploaded["content_type"] = content_type
        return f"{BUCKET_PUBLIC}/{key}"

    monkeypatch.setattr(mirror, "_fetch_and_upload", fake_fetch)

    # Mock 供应商返回 `https://mock.cdn.example.com/audio.mp3` —— 该域名解析不出公网 IP，
    # 回源前的 SSRF 守卫按 fail-closed 拒掉（**这是正确行为**，见 test_mirror_blocks_ssrf_targets）。
    # 要测「生成即回源」这一条，就让 mock 的轮询结果返回一个可解析的公网链接。
    mock = get_orchestrator()
    real_get = mock.get

    async def fake_get(external_id):
        res = await real_get(external_id)
        if res.status == "completed":
            res = res.model_copy(update={"audio_url": UPSTREAM})
        return res

    monkeypatch.setattr(mock, "get", fake_get)

    h = _register(client, "cdn-gen@test.com")
    _subscribe(client, h)
    r = client.post("/api/songs/generate", json={"prompt": "cdn", "style": "pop"}, headers=h)
    sid = r.json()["song_id"]
    asyncio.run(_process_song(sid))

    body = client.get(f"/api/songs/{sid}", headers=h).json()
    assert body["status"] == "completed", (body, uploaded)
    assert uploaded["src"] == UPSTREAM, "回源应取上游 audio_url"
    assert uploaded["key"].endswith(f"/{sid}.mp3"), uploaded
    assert uploaded["key"].startswith("songs/"), uploaded
    assert uploaded["content_type"].startswith("audio/"), uploaded
    assert body["audio_url"].startswith(BUCKET_PUBLIC), body["audio_url"]


def test_generate_without_storage_keeps_upstream_url(client, no_storage, monkeypatch):
    """未配 storage：生成照常完成，链接仍是上游（零回归）。"""
    from app.routers.songs import _process_song

    h = _register(client, "cdn-gen-nostorage@test.com")
    _subscribe(client, h)
    r = client.post("/api/songs/generate", json={"prompt": "cdn", "style": "pop"}, headers=h)
    sid = r.json()["song_id"]
    asyncio.run(_process_song(sid))
    body = client.get(f"/api/songs/{sid}", headers=h).json()
    assert body["status"] == "completed"
    assert body["audio_url"]  # 有链接（上游或 preview），关键是**没有 500**


# ═══════════════════════════════════════════════════════════════════════════
# 5. 下载 / 试听 / FLOW 同步走 CDN 出口
# ═══════════════════════════════════════════════════════════════════════════
def test_download_uses_cdn_for_mp3(client, storage_configured, monkeypatch, tmp_path):
    email = "cdn-dl@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(
            db, _uid(db, email), audio_cdn_url=f"{BUCKET_PUBLIC}/songs/3/3.mp3"
        )
    finally:
        db.close()

    seen = {}
    fake = tmp_path / "out.mp3"
    fake.write_bytes(b"ID3\x03" + b"\x00" * 32)

    def fake_dl(url, **kw):
        seen["url"] = url
        return str(fake)

    monkeypatch.setattr("app.storage.s3.download_to_temp", fake_dl)
    r = client.get(f"/api/songs/{song.id}/download", headers=h)
    assert r.status_code == 200, r.text
    assert seen["url"] == f"{BUCKET_PUBLIC}/songs/3/3.mp3"


def test_download_keeps_upstream_when_no_cdn(client, storage_configured, monkeypatch, tmp_path):
    email = "cdn-dl-null@test.com"
    h = _register(client, email)
    _subscribe(client, h)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), audio_cdn_url="")
    finally:
        db.close()
    seen = {}
    fake = tmp_path / "out.mp3"
    fake.write_bytes(b"ID3\x03")
    monkeypatch.setattr(
        "app.storage.s3.download_to_temp",
        lambda url, **kw: (seen.__setitem__("url", url), str(fake))[1],
    )
    r = client.get(f"/api/songs/{song.id}/download", headers=h)
    assert r.status_code == 200, r.text
    assert seen["url"] == UPSTREAM


def test_sync_flow_uses_cdn_and_degrades_gracefully(client, no_storage):
    """FLOW 歌单：未配 storage → 仍然 200 + 上游链接（不 500）。"""
    from tests.test_api import _link_and_exchange

    email = "cdn-flow@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email))  # completed + audio_url=UPSTREAM + 已审核
        sid = song.id
    finally:
        db.close()

    hs = _link_and_exchange(client, h)
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert r.status_code == 200, r.text
    assert r.json()["audio_cdn_url"] == UPSTREAM  # 降级：上游
    r = client.get("/api/v1/sync/flow", headers=hs)
    assert any(item["song_id"] == sid for item in r.json())


def test_sync_flow_mirrors_when_configured(client, storage_configured, monkeypatch):
    from tests.test_api import _link_and_exchange

    monkeypatch.setattr(
        mirror,
        "_fetch_and_upload",
        lambda src, key, content_type, storage_class, song_ref: f"{BUCKET_PUBLIC}/{key}",
    )
    email = "cdn-flow2@test.com"
    h = _register(client, email)
    db = SessionLocal()
    try:
        song = _mk_song(db, _uid(db, email), audio_cdn_url="")
        sid = song.id
    finally:
        db.close()

    hs = _link_and_exchange(client, h)
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert r.status_code == 200, r.text
    assert r.json()["audio_cdn_url"].startswith(BUCKET_PUBLIC)


# ═══════════════════════════════════════════════════════════════════════════
# 6. 归档元数据 / 脚本闸门
# ═══════════════════════════════════════════════════════════════════════════
def test_song_model_has_archive_columns():
    cols = {c.name for c in Song.__table__.columns}
    assert "archived_at" in cols
    assert "storage_class" in cols


def test_archive_script_refuses_glacier(tmp_path, capsys, monkeypatch):
    """GLACIER 类不支持直接 GET → 脚本必须拒绝，而不是把歌变成「点了播不了」。"""
    import scripts.archive_cdn as arch

    monkeypatch.setattr(arch.storage, "is_configured", lambda: True)
    code = arch.main(["--class", "GLACIER", "--dry-run"])
    assert code == 2
    assert "GLACIER" in capsys.readouterr().err


def test_archive_script_early_exits_when_disabled(capsys, monkeypatch):
    """归档阈值 <=0（关闭）→ 早退码 2 + 明确提示，而不是静默跑一遍。"""
    import scripts.archive_cdn as arch

    monkeypatch.setattr(arch.storage, "is_configured", lambda: True)
    code = arch.main(["--days", "0"])
    assert code == 2
    assert "关闭" in capsys.readouterr().err


def test_backfill_script_dry_run_lists_pending_ids(capsys, storage_configured, monkeypatch):
    """回填脚本：dry-run 只列 id、不打网络；无待办时明确输出。"""
    import scripts.backfill_cdn as bf

    db = SessionLocal()
    try:
        song = _mk_song(db, 999006, audio_cdn_url="")
        sid = song.id
    finally:
        db.close()

    code = bf.main(["--dry-run", "--ids", str(sid)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert f"song_id={sid}" in out
    assert "dry-run" in out


def test_backfill_script_early_exit_without_storage(capsys, no_storage):
    """未配 storage → 回填脚本早退码 2（提示先配 storage_*），不是空跑。"""
    import scripts.backfill_cdn as bf

    code = bf.main([])
    assert code == 2
    assert "对象存储未配置" in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════════════════
# 7. 归档作业（存储类别迁移）用 monkeypatch 走完全流程
# ═══════════════════════════════════════════════════════════════════════════
def test_archive_migrates_old_song_and_is_idempotent(storage_configured, monkeypatch):
    import scripts.archive_cdn as arch

    calls = []
    monkeypatch.setattr(
        storage, "set_storage_class", lambda key, cls: calls.append((key, cls))
    )
    db = SessionLocal()
    try:
        old = _mk_song(
            db,
            999007,
            audio_cdn_url=f"{BUCKET_PUBLIC}/songs/999007/old.mp3",
            created_at=datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=200),
        )
        fresh = _mk_song(
            db,
            999008,
            audio_cdn_url=f"{BUCKET_PUBLIC}/songs/999008/new.mp3",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        old_id, fresh_id = old.id, fresh.id
    finally:
        db.close()

    monkeypatch.setattr(arch.storage, "is_configured", lambda: True)
    assert arch.main(["--days", "90"]) == 0
    assert ("songs/999007/old.mp3", "STANDARD_IA") in calls
    # 新歌不该被归档
    assert all("999008" not in key for key, _ in calls)

    db = SessionLocal()
    try:
        assert db.get(Song, old_id).archived_at is not None
        assert db.get(Song, old_id).storage_class == "STANDARD_IA"
        assert db.get(Song, fresh_id).archived_at is None
    finally:
        db.close()

    # 幂等：再跑一次不会重复迁移已归档的曲目
    calls.clear()
    assert arch.main(["--days", "90"]) == 0
    assert all("999007" not in key for key, _ in calls)


def test_archive_unarchive_heats_object_back(storage_configured, monkeypatch):
    """回热：把已归档对象搬回标准存储，并清空 archived_at。"""
    import scripts.archive_cdn as arch

    calls = []
    monkeypatch.setattr(
        storage, "set_storage_class", lambda key, cls: calls.append((key, cls))
    )
    db = SessionLocal()
    try:
        s = _mk_song(
            db,
            999009,
            audio_cdn_url=f"{BUCKET_PUBLIC}/songs/999009/old.mp3",
            archived_at=datetime.datetime.now(datetime.timezone.utc),
            storage_class="STANDARD_IA",
        )
        sid = s.id
    finally:
        db.close()

    monkeypatch.setattr(arch.storage, "is_configured", lambda: True)
    assert arch.main(["--unarchive", "--ids", str(sid)]) == 0
    assert ("songs/999009/old.mp3", "STANDARD") in calls
    db = SessionLocal()
    try:
        after = db.get(Song, sid)
        assert after.archived_at is None
        assert after.storage_class == "STANDARD"
    finally:
        db.close()
