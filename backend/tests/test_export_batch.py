"""GAP-028 批量导出：多选 zip + 元数据 CSV。

钉住的契约（每条都对应一个真会出事的面）：
1. **越权 403**：导出物 = 完整音频 + 全部元数据，比单曲下载敏感。传别人的 id
   → 403（不是 404/不是静默跳过）；传不存在的 id → 403（不泄露存在性）。
2. **CSV 编码**：UTF-8 **带 BOM**（否则 Excel 打开中文全乱码）；
   字段与 SongOut 对齐；公式前缀（= + - @）被中和（CWE-1236）。
3. **未配 storage 优雅降级**：prod 现在就是 0 条 storage_*。导出必须仍可用
   （本地临时包 + 一次性 token），**绝不 500**；配了 storage 则走 presign。
4. **上限防护**：曲目数硬 400；单曲超限跳过并如实汇报（不整批失败、不静默丢）。
5. **回收站 / 未完成**：已删除的进不了导出；未完成的 400（状态问题非权限问题）。
"""

import io
import os
import zipfile

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Song, SongLike, User
from app.routers import export as export_router
from app.storage import s3 as storage

UPSTREAM = "https://example.com/audio/song.mp3"


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
        title=kw.pop("title", "导出测试曲"),
        style=kw.pop("style", "synthwave"),
        status=kw.pop("status", "completed"),
        audio_url=kw.pop("audio_url", UPSTREAM),
        duration=kw.pop("duration", 123),
        moderation_status="approved",
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def no_storage(monkeypatch):
    """prod 现状：storage_* 全空 → 本地降级路径。"""
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


@pytest.fixture
def fake_audio(monkeypatch, tmp_path):
    """把远程音频下载换成本地小文件（本文件不打任何外部请求）。"""
    payload = b"ID3\x03" + b"\x00" * 2048

    def _fake(url, **kw):
        fd, p = __import__("tempfile").mkstemp(suffix=".mp3")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        return p

    monkeypatch.setattr("app.storage.s3.download_to_temp", _fake)
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# 1. 越权 403
# ═══════════════════════════════════════════════════════════════════════════


def test_export_other_users_song_is_403(client, no_storage, fake_audio):
    """别人的曲目 → 403（明确拒绝，而非 404 静默）。"""
    h_a = _register(client, "exp-a@test.com")
    _register(client, "exp-b@test.com")
    db = SessionLocal()
    mine = _mk_song(db, _uid(db, "exp-a@test.com"), title="我的")
    other = _mk_song(db, _uid(db, "exp-b@test.com"), title="别人的")
    my_id, other_id = mine.id, other.id
    db.close()

    ok = client.post("/api/songs/export", json={"song_ids": [my_id]}, headers=h_a)
    assert ok.status_code == 200, ok.text

    bad = client.post("/api/songs/export", json={"song_ids": [my_id, other_id]}, headers=h_a)
    assert bad.status_code == 403, bad.text
    assert "只能导出你自己" in bad.json()["detail"]

    csv_bad = client.get(f"/api/songs/export.csv?ids={other_id}", headers=h_a)
    assert csv_bad.status_code == 403, csv_bad.text


def test_export_unknown_id_is_403_not_404(client, no_storage, fake_audio):
    """不存在的 id → 403（与「不是你的」同形，不泄露 id 是否存在）。"""
    h = _register(client, "exp-unknown@test.com")
    r = client.post("/api/songs/export", json={"song_ids": [999999]}, headers=h)
    assert r.status_code == 403, r.text


def test_export_requires_auth(client, no_storage):
    assert client.post("/api/songs/export", json={"song_ids": [1]}).status_code == 401
    assert client.get("/api/songs/export.csv").status_code == 401


def test_export_soft_deleted_song_is_403(client, no_storage, fake_audio):
    """回收站里的歌「已删除但可恢复」→ 导出会架空删除语义，403。"""
    h = _register(client, "exp-trash@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-trash@test.com")).id
    db.close()
    assert client.delete(f"/api/songs/{sid}", headers=h).status_code == 200

    r = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert r.status_code == 403, r.text


def test_export_unfinished_song_is_400_not_403(client, no_storage, fake_audio):
    """未完成 = 状态问题（没音频可打包），不是权限问题 → 400。"""
    h = _register(client, "exp-pending@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-pending@test.com"), status="processing").id
    db.close()
    r = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert r.status_code == 400, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 2. CSV：BOM + 字段 + 注入中和
# ═══════════════════════════════════════════════════════════════════════════


def test_csv_has_utf8_bom_and_expected_columns(client, no_storage):
    h = _register(client, "exp-csv@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-csv@test.com")
    sid = _mk_song(db, uid, title="中文歌名", style="古风").id
    db.add(SongLike(song_id=sid, user_id=uid))
    db.commit()
    db.close()

    r = client.get(f"/api/songs/export.csv?ids={sid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"\xef\xbb\xbf"), "必须带 UTF-8 BOM，否则 Excel 中文乱码"
    text = r.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    for col in ("id", "title", "style", "duration", "created_at", "play_count", "like_count"):
        assert col in header, f"缺少字段 {col}"
    row = text.splitlines()[1]
    assert "中文歌名" in row and "古风" in row
    assert "attachment" in r.headers.get("content-disposition", "")


def test_csv_escapes_formula_prefix(client, no_storage):
    """CWE-1236：标题以 = 开头会让 Excel 执行公式。"""
    h = _register(client, "exp-inject@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-inject@test.com"), title="=cmd|'/C calc'!A1").id
    db.close()
    r = client.get(f"/api/songs/export.csv?ids={sid}", headers=h)
    row = r.content.decode("utf-8-sig").splitlines()[1]
    assert "'=cmd" in row, "公式前缀必须被单引号中和"
    assert row.split(",")[1].startswith("'"), "title 列应以单引号开头"


def test_csv_default_exports_whole_library(client, no_storage):
    h = _register(client, "exp-all@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-all@test.com")
    for i in range(3):
        _mk_song(db, uid, title=f"曲{i}")
    db.commit()
    db.close()
    r = client.get("/api/songs/export.csv", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.content.decode("utf-8-sig").splitlines()) == 4  # 表头 + 3 行


# ═══════════════════════════════════════════════════════════════════════════
# 3. 未配 storage → 优雅降级（绝不 500）
# ═══════════════════════════════════════════════════════════════════════════


def test_export_without_storage_degrades_to_local_token(client, no_storage, fake_audio):
    export_router.local_exports.reset()
    h = _register(client, "exp-local@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-local@test.com")
    ids = [_mk_song(db, uid, title=f"本地{i}").id for i in range(2)]
    db.close()

    r = client.post("/api/songs/export", json={"song_ids": ids}, headers=h)
    assert r.status_code == 200, f"未配 storage 必须降级而非报错：{r.text}"
    body = r.json()
    assert body["storage"] == "local"
    assert body["url"].startswith("/api/songs/export/download/")
    assert body["song_count"] == 2 and body["expires_at_iso"]

    dl = client.get(body["url"], headers=h)
    assert dl.status_code == 200, dl.text
    assert dl.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        names = zf.namelist()
        assert "metadata.csv" in names
        assert sum(1 for n in names if n.endswith(".mp3")) == 2

    # 一次性：再领即 404
    assert client.get(body["url"], headers=h).status_code == 404


def test_local_export_token_is_owner_bound(client, no_storage, fake_audio):
    """token 泄露给别的登录用户也拿不到包。"""
    export_router.local_exports.reset()
    h = _register(client, "exp-own1@test.com")
    h2 = _register(client, "exp-own2@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-own1@test.com")).id
    db.close()
    url = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h).json()["url"]
    assert client.get(url, headers=h2).status_code == 404
    assert client.get(url, headers=h).status_code == 200  # 未被他人领取掉


def test_export_with_storage_returns_presigned_url(client, fake_audio, monkeypatch):
    """配了 storage → 上传 + presign（一次性签名链接），形状与本地路径一致。"""
    monkeypatch.setattr(settings, "storage_endpoint", "https://cos.test")
    monkeypatch.setattr(settings, "storage_bucket", "music-bucket")
    monkeypatch.setattr(settings, "storage_access_key", "ak")
    monkeypatch.setattr(settings, "storage_secret_key", "sk")
    assert storage.is_configured() is True

    uploaded: list[tuple] = []
    monkeypatch.setattr(
        storage, "upload_file", lambda p, k, ct="application/octet-stream", **kw: uploaded.append((k, ct)) or "ok"
    )
    monkeypatch.setattr(
        storage, "presigned_url", lambda key, expires_in=3600: f"https://cos.test/signed/{key}?sig=x"
    )

    h = _register(client, "exp-s3@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-s3@test.com")).id
    db.close()
    r = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storage"] == "s3"
    assert body["url"].startswith("https://cos.test/signed/")
    assert uploaded and uploaded[0][0].startswith("exports/"), uploaded


def test_export_storage_failure_falls_back_not_500(client, fake_audio, monkeypatch):
    """storage 配了但上传炸了 → 降级本地包，绝不 500。"""
    export_router.local_exports.reset()
    monkeypatch.setattr(settings, "storage_endpoint", "https://cos.test")
    monkeypatch.setattr(settings, "storage_bucket", "music-bucket")
    monkeypatch.setattr(settings, "storage_access_key", "ak")
    monkeypatch.setattr(settings, "storage_secret_key", "sk")

    def _boom(*a, **kw):
        raise RuntimeError("upstream 503")

    monkeypatch.setattr(storage, "upload_file", _boom)

    h = _register(client, "exp-s3fail@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-s3fail@test.com")).id
    db.close()
    r = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["storage"] == "local"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 上限防护
# ═══════════════════════════════════════════════════════════════════════════


def test_export_rejects_too_many_songs(client, no_storage, fake_audio):
    h = _register(client, "exp-many@test.com")
    ids = list(range(1, export_router.EXPORT_MAX_SONGS + 2))
    r = client.post("/api/songs/export", json={"song_ids": ids}, headers=h)
    assert r.status_code == 400, r.text
    assert "最多" in r.json()["detail"]


def test_export_rejects_empty_and_duplicate_only_payload(client, no_storage):
    h = _register(client, "exp-empty@test.com")
    assert client.post("/api/songs/export", json={"song_ids": []}, headers=h).status_code == 400


def test_duplicate_ids_collapse(client, no_storage, fake_audio):
    """重复 id 不能让 zip 里的曲目数与勾选数对不上。"""
    export_router.local_exports.reset()
    h = _register(client, "exp-dup@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-dup@test.com")).id
    db.close()
    body = client.post("/api/songs/export", json={"song_ids": [sid, sid, sid]}, headers=h).json()
    assert body["song_count"] == 1


def test_oversized_song_is_skipped_not_fatal(client, no_storage, monkeypatch):
    """单曲超限 → 跳过该曲 + export-notes.txt 如实汇报，其余照常打包。"""
    export_router.local_exports.reset()
    monkeypatch.setattr(settings, "export_max_song_mb", 1, raising=False)

    def _fake(url, *, max_bytes=None, **kw):
        import tempfile

        if "big" in url:
            if max_bytes is not None:
                raise ValueError(f"文件过大（超过 {max_bytes} bytes）")
        fd, p = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, "wb") as f:
            f.write(b"ID3" + b"\x00" * 128)
        return p

    monkeypatch.setattr("app.storage.s3.download_to_temp", _fake)

    h = _register(client, "exp-big@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-big@test.com")
    small = _mk_song(db, uid, title="小的", audio_url="https://example.com/a.mp3").id
    big = _mk_song(db, uid, title="大的", audio_url="https://example.com/big.mp3").id
    db.close()

    body = client.post("/api/songs/export", json={"song_ids": [small, big]}, headers=h).json()
    assert body["song_count"] == 2  # 两首都登记了（大的进 skipped）
    assert body["skipped"], "超限曲目必须如实汇报"
    dl = client.get(body["url"], headers=h)
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        assert "export-notes.txt" in zf.namelist()
        assert sum(1 for n in zf.namelist() if n.endswith(".mp3")) == 1


def test_zip_entry_names_are_sanitized(client, no_storage, fake_audio):
    """标题里的路径分隔符不得变成 zip 内的目录穿越。"""
    export_router.local_exports.reset()
    h = _register(client, "exp-name@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-name@test.com")
    sid = _mk_song(db, uid, title="../../etc/passwd").id
    db.close()
    body = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h).json()
    dl = client.get(body["url"], headers=h)
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        for n in zf.namelist():
            assert ".." not in n and not n.startswith("/"), n


# ═══════════════════════════════════════════════════════════════════════════
# 5. 打包内容与元数据同源
# ═══════════════════════════════════════════════════════════════════════════


def test_zip_metadata_csv_matches_endpoint(client, no_storage, fake_audio):
    """zip 内的 metadata.csv 与 /export.csv **同一份**（字段不漂移）。"""
    export_router.local_exports.reset()
    h = _register(client, "exp-parity@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-parity@test.com")
    s = _mk_song(db, uid, title="同源曲", play_count=7)
    sid = s.id
    db.close()

    zip_body = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h).json()
    dl = client.get(zip_body["url"], headers=h)
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        inside = zf.read("metadata.csv").decode("utf-8-sig")

    direct = client.get(f"/api/songs/export.csv?ids={sid}", headers=h).content.decode("utf-8-sig")
    assert inside.splitlines()[0] == direct.splitlines()[0], "表头必须一致"
    assert inside.splitlines()[1] == direct.splitlines()[1], "数据行必须一致"


def test_interaction_counts_in_csv(client, no_storage):
    h = _register(client, "exp-like@test.com")
    db = SessionLocal()
    uid = _uid(db, "exp-like@test.com")
    sid = _mk_song(db, uid, title="被赞的").id
    for i in range(2):
        u = User(email=f"liker{i}@test.com", hashed_password="x")
        db.add(u)
        db.flush()
        db.add(SongLike(song_id=sid, user_id=u.id))
    db.commit()
    db.close()
    row = client.get(f"/api/songs/export.csv?ids={sid}", headers=h).content.decode("utf-8-sig").splitlines()[1]
    assert ",2," in row, f"like_count 应为 2：{row}"


def test_export_rate_limited(monkeypatch, client, no_storage, fake_audio):
    """限流开启时，超过每分钟上限 → 429（防脚本循环导出打满带宽）。"""
    from app.ratelimit import _hits

    _hits.clear()
    monkeypatch.setattr(settings, "enable_rate_limit", True)
    monkeypatch.setattr(settings, "export_rate_limit_per_minute", 1)
    h = _register(client, "exp-rl@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "exp-rl@test.com")).id
    db.close()
    first = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert first.status_code == 200, first.text
    second = client.post("/api/songs/export", json={"song_ids": [sid]}, headers=h)
    assert second.status_code == 429, second.text
