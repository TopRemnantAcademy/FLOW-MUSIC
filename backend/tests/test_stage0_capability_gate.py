"""规划 B 阶段 0 + 阶段 1 行为锁定测试。

阶段 0（2026-09-12）：引擎能力 fail-closed —— 缺失能力 → 501，而非后台 AttributeError → 500，
                    或 task 被忽略后静默产出「假功能」。
阶段 1（2026-09-12）：MurekaProvider 接通**真实 Mureka 端点形状** ——
                    create() 按 task 分流到 /song/extend、/song/remix（cover→remix），
                    并新增 files_upload / vocal_clone / stem / region_edit / instrumental / easy_generate。

两条硬性约束（来自官方文档逐端点核实，见 PLAN_H5_AND_MUREKA_GAPS.md B.5.3）：
  · /song/extend 的 model **仅接受 mureka-7.6 / mureka-8**（我们的默认 mureka-9 会 400 → 必须降级）。
  · /song/remix 请求体**无 model 字段**；lyrics(≤5000) 与 prompt(≤1024) 均为必填。
  · /song/extend 的 lyrics ≤3000；extend_at 单位为**毫秒**。
  · region-edit 改写区间必须 ≥3000ms。
  · instrumental 的 model **不含 mureka-o2**，且轮询走**独立** /instrumental/query。

所有网络调用均以 monkeypatch 打桩，**不发起真实请求**。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.orchestrator.base import GenerateRequest
from app.orchestrator.client import set_orchestrator
from app.orchestrator.mock import MockProvider
from app.orchestrator.mureka import MurekaProvider


@pytest.fixture()
def mureka_engine():
    """临时把编排层切到真实 MurekaProvider（网络一律打桩）。"""
    set_orchestrator(MurekaProvider())
    yield
    set_orchestrator(MockProvider())  # 还原 conftest 注入的 mock


def _auth_headers(client: TestClient, email: str) -> dict:
    pw = "Password123!"
    client.post("/api/auth/register", json={"email": email, "password": pw})
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_song(email: str, lyric: str = "orig lyric") -> int:
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    s = Song(
        user_id=u.id,
        title="t",
        status="completed",
        custom_id="mock-custom-0001",
        audio_url="orig.mp3",
        lyric=lyric,
        task_type="generate",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    db.close()
    return s.id


def _capture_post(monkeypatch, response=None):
    """打桩 MurekaProvider._post，捕获 (path, json) 并返回假任务。"""
    captured = {}

    async def fake_post(self, path, json=None):
        captured["path"] = path
        captured["json"] = json
        return response or {"id": "fake-task-1", "model": "mureka-8"}

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)
    return captured


def _capture_multipart(monkeypatch, response=None):
    captured = {}

    async def fake_mp(self, path, data=None, files=None):
        captured.update(path=path, data=data, files=files)
        return response or {"id": "fake-file-1"}

    monkeypatch.setattr(MurekaProvider, "_post_multipart", fake_mp)
    return captured


# ============================================================
# 阶段 0 残留：Mureka 仍**不具备**的能力 → 501（诚实 fail-closed）
# ============================================================
@pytest.mark.parametrize(
    "path,body",
    [
        ("crop", {"start_time": 0, "end_time": 10}),
        ("speed", {"speed": 1.5}),
        ("aligned-lyrics", {"lyrics": "hi"}),
        ("whole-song", {}),
    ],
)
def test_unsupported_postprocess_still_501(client, mureka_engine, path, body):
    """crop/speed/aligned-lyrics/whole-song：Mureka 无对应端点 → 保持 501。"""
    email = f"pp-{path}@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/{path}", json=body, headers=h)
    assert r.status_code == 501, r.text


def test_sound_still_501(client, mureka_engine):
    """sound 是 Suno 独有端点，Mureka 无 → 保持 501。"""
    h = _auth_headers(client, "snd501@test.com")
    r = client.post("/api/songs/sound", json={"title": "sfx", "tags": "rain"}, headers=h)
    assert r.status_code == 501, r.text


# ============================================================
# 阶段 1：已接通的能力 → 不再 501
# ============================================================
@pytest.mark.parametrize(
    "task,expected_path",
    [
        ("extend", "/song/extend"),
        ("remix", "/song/remix"),
        ("cover", "/song/remix"),  # Mureka 无 cover 端点 → 映射为 remix
    ],
)
def test_spawn_tasks_no_longer_501(client, mureka_engine, monkeypatch, task, expected_path):
    captured = _capture_post(monkeypatch)

    async def fake_get(self, path):
        return {
            "id": "fake-task-1",
            "status": "succeeded",
            "model": "mureka-8",
            "choices": [{"url": "https://cdn.example.com/a.mp3", "duration": 120000, "id": "c1"}],
        }

    monkeypatch.setattr(MurekaProvider, "_get", fake_get)
    monkeypatch.setattr(settings, "enable_preview_clip", False)
    monkeypatch.setattr(settings, "enable_audio_postprocess", False)

    email = f"ok-{task}@test.com"
    h = _auth_headers(client, email)
    sid = _make_song(email)
    r = client.post(f"/api/songs/{sid}/{task}", json={}, headers=h)
    assert r.status_code == 200, r.text
    # 关键：确实打到了真实 Mureka 端点，且带上了父歌 id（不再是与父歌无关的假功能）
    assert captured["path"] == expected_path, captured
    assert captured["json"]["song_id"] == "mock-custom-0001"


def test_upload_no_longer_501(client, mureka_engine, monkeypatch):
    """upload → Mureka /files/upload（multipart, purpose=audio）。"""
    captured = _capture_multipart(monkeypatch)
    h = _auth_headers(client, "upl-ok@test.com")
    r = client.post("/api/songs/upload", json={"audio_url": "https://x.com/a.mp3"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["custom_id"] == "fake-file-1"
    assert captured["path"] == "/files/upload"
    assert captured["data"]["purpose"] == "audio"  # 供 song extension 使用
    assert captured["data"]["url"] == "https://x.com/a.mp3"


def test_spawn_requires_lyric(client, mureka_engine):
    """Mureka remix/extend 硬性要求歌词；父歌无词且未填 → 明确 400（而非静默假功能）。"""
    h = _auth_headers(client, "nolyr@test.com")
    sid = _make_song("nolyr@test.com", lyric="")
    r = client.post(f"/api/songs/{sid}/remix", json={}, headers=h)
    assert r.status_code == 400, r.text
    assert "歌词" in r.json()["detail"]


# ============================================================
# 阶段 1：create() 路由与载荷（单元级，直接断言请求体）
# ============================================================
def test_create_routes_extend(monkeypatch):
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="extend", continue_song_id="parent-1", lyric="la la la")
    res = asyncio.run(MurekaProvider().create(req))
    assert cap["path"] == "/song/extend"
    j = cap["json"]
    assert j["song_id"] == "parent-1"
    assert j["lyrics"] == "la la la"
    assert j["extend_type"] == "tail"
    assert j["extend_at"] == 420000  # 未指定 clip_seconds → tail 取上限（官方会按时长截断）
    assert j["model"] == "mureka-8"  # 默认 mureka-9 对 extend 非法 → 降级
    assert res.status == "processing"
    assert res.external_id == "fake-task-1"


def test_extend_model_downgraded_from_mureka9(monkeypatch):
    """/song/extend 仅接受 7.6/8：显式传 mureka-9 也必须降级，否则官方 400。"""
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="extend", continue_song_id="p", lyric="x", model_version="mureka-9")
    asyncio.run(MurekaProvider().create(req))
    assert cap["json"]["model"] == "mureka-8"


def test_extend_model_keeps_76(monkeypatch):
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(
        task="extend", continue_song_id="p", lyric="x", model_version="mureka-7.6"
    )
    asyncio.run(MurekaProvider().create(req))
    assert cap["json"]["model"] == "mureka-7.6"


@pytest.mark.parametrize(
    "clip_seconds,expected_ms",
    [
        (30, 30000),  # 秒 → 毫秒
        (1, 8000),  # 低于下限 → clamp 到 8000
        (9999, 420000),  # 高于上限 → clamp 到 420000
    ],
)
def test_extend_at_converted_and_clamped(monkeypatch, clip_seconds, expected_ms):
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="extend", continue_song_id="p", lyric="x", clip_seconds=clip_seconds)
    asyncio.run(MurekaProvider().create(req))
    assert cap["json"]["extend_at"] == expected_ms


def test_create_routes_remix(monkeypatch):
    cap = _capture_post(monkeypatch)
    p = MurekaProvider()
    req = GenerateRequest(task="remix", continue_song_id="parent-1", lyric="lyric", style="jazz")
    asyncio.run(p.create(req))
    assert cap["path"] == "/song/remix"
    j = cap["json"]
    assert j["song_id"] == "parent-1"
    assert j["lyrics"] == "lyric"
    assert "jazz" in j["prompt"]
    assert "model" not in j  # remix 请求体官方无 model 字段
    assert j["n"] == p.n


def test_cover_maps_to_remix(monkeypatch):
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="cover", continue_song_id="parent-1", lyric="l", style="rock")
    asyncio.run(MurekaProvider().create(req))
    assert cap["path"] == "/song/remix"  # Mureka 无 cover 端点
    assert "rock" in cap["json"]["prompt"]


def test_create_routes_generate_unchanged(monkeypatch):
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="generate", lyric="hello", style="pop")
    asyncio.run(MurekaProvider().create(req))
    assert cap["path"] == "/song/generate"
    assert cap["json"]["lyrics"] == "hello"


def test_lyrics_truncated_per_endpoint_limits(monkeypatch):
    cap = _capture_post(monkeypatch)
    asyncio.run(
        MurekaProvider().create(
            GenerateRequest(task="extend", continue_song_id="p", lyric="a" * 5000)
        )
    )
    assert len(cap["json"]["lyrics"]) == 3000  # extend ≤3000

    cap2 = _capture_post(monkeypatch)
    asyncio.run(
        MurekaProvider().create(
            GenerateRequest(task="remix", continue_song_id="p", lyric="b" * 9000)
        )
    )
    assert len(cap2["json"]["lyrics"]) == 5000  # remix ≤5000


def test_missing_continue_id_degrades_to_generate(monkeypatch):
    """无父歌基准时退化为普通 generate（并留 warning），但仍不会静默冒充「续写」。"""
    cap = _capture_post(monkeypatch)
    req = GenerateRequest(task="extend", continue_song_id=None, lyric="x")
    asyncio.run(MurekaProvider().create(req))
    assert cap["path"] == "/song/generate"


# ============================================================
# 阶段 1：新增能力的形状（同步 / multipart / 校验）
# ============================================================
def test_region_edit_payload_and_interval(monkeypatch):
    p = MurekaProvider()
    with pytest.raises(ValueError):
        asyncio.run(p.region_edit("s", "x", 0, 2000))  # 区间 <3000ms → 拒绝

    cap = _capture_post(monkeypatch)
    asyncio.run(p.region_edit("s1", "new lyric", 30000, 50000))
    assert cap["path"] == "/song/region-edit"
    assert cap["json"]["lyrics"] == "new lyric"
    assert cap["json"]["edit_start"] == 30000
    assert cap["json"]["edit_end"] == 50000


def test_instrumental_model_downgrade(monkeypatch):
    """instrumental 的 model 不含 mureka-o2 → 降级为 auto。"""
    cap = _capture_post(monkeypatch)
    asyncio.run(MurekaProvider().instrumental("ambient", model="mureka-o2"))
    assert cap["path"] == "/instrumental/generate"
    assert cap["json"]["model"] == "auto"


def test_instrumental_uses_separate_query(monkeypatch):
    """instrumental 轮询必须走独立端点 /instrumental/query，不能复用 /song/query。"""
    seen = {}

    async def fake_get(self, path):
        seen["path"] = path
        return {"id": "t", "status": "succeeded", "choices": [{"url": "u", "duration": 1000}]}

    monkeypatch.setattr(MurekaProvider, "_get", fake_get)
    asyncio.run(MurekaProvider().get_instrumental("inst-1"))
    assert seen["path"] == "/instrumental/query/inst-1"


def test_stem_is_synchronous(monkeypatch):
    """stem 是同步接口：直接返回 zip_url + expires_at，无 task_id / 无需轮询。"""

    async def fake_post(self, path, json=None):
        return {
            "zip_url": "https://x/z.zip",
            "expires_at": 1700000000,
            "midi_zip_url": "https://x/m.zip",
        }

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)
    out = asyncio.run(MurekaProvider().stem("https://x/a.mp3"))
    assert out["zip_url"] == "https://x/z.zip"
    assert out["expires_at"] == 1700000000
    assert out["midi_zip_url"] == "https://x/m.zip"


def test_vocal_clone_is_multipart(monkeypatch):
    captured = _capture_multipart(monkeypatch, response={"id": "vocal-1"})
    vid = asyncio.run(MurekaProvider().vocal_clone(b"123", "v.m4a"))
    assert vid == "vocal-1"
    assert captured["path"] == "/vocal/clone"  # 注意：不是 /song/vocal-clone
    assert "file" in captured["files"]


def test_files_upload_requires_source():
    with pytest.raises(ValueError):
        asyncio.run(MurekaProvider().files_upload(purpose="audio"))


def test_easy_generate_payload(monkeypatch):
    cap = _capture_post(monkeypatch)
    asyncio.run(MurekaProvider().easy_generate("a song", styles=["gospel", "lo-fi"]))
    assert cap["path"] == "/song/easy-generate"
    assert cap["json"]["styles"] == ["gospel", "lo-fi"]
    assert "lyrics" not in cap["json"]  # easy-generate 无 lyrics 字段


# ============================================================
# 能力声明 & Mock 回归
# ============================================================
def test_mureka_capability_declarations():
    p = MurekaProvider()
    for t in ("generate", "custom", "extend", "cover", "remix"):
        assert t in p.supported_tasks, t
    # 仍不具备（Mureka 无对应端点）→ 保持 501
    for cap in ("crop", "speed", "whole_song", "aligned_lyrics", "sound"):
        assert p.supports(cap) is False, cap
    # 阶段 1 新增能力
    for cap in (
        "upload",
        "files_upload",
        "vocal_clone",
        "stem",
        "region_edit",
        "instrumental",
        "easy_generate",
    ):
        assert p.supports(cap) is True, cap


def test_mock_unaffected(client):
    """回归保护：MockProvider 声明全能力，不应被 fail-closed 误伤。"""
    h = _auth_headers(client, "mockreg@test.com")
    sid = _make_song("mockreg@test.com")
    assert (
        client.post(
            f"/api/songs/{sid}/crop", json={"start_time": 0, "end_time": 10}, headers=h
        ).status_code
        == 200
    )
    assert client.post(f"/api/songs/{sid}/extend", json={}, headers=h).status_code == 200
    assert (
        client.post(
            "/api/songs/upload", json={"audio_url": "https://x.com/a.mp3"}, headers=h
        ).status_code
        == 200
    )


# ============================================================
# 阶段 2：新端点（REST 层）
# ============================================================
def _fast_poll(monkeypatch):
    """让后台轮询在测试里即时完成：interval=0（_poll_task 内部下限 0.05s）。

    ⚠️ 必须覆盖默认值 interval=8 / timeout=240，否则单条用例要睡 8 秒。
    """
    monkeypatch.setattr(settings, "song_poll_interval", 0)
    monkeypatch.setattr(settings, "song_poll_timeout", 5)
    monkeypatch.setattr(settings, "enable_preview_clip", False)
    monkeypatch.setattr(settings, "enable_audio_postprocess", False)


def _stub_poll(monkeypatch):
    """打桩 _get：记录轮询路径并返回 succeeded（含 choices）。"""
    seen = {}

    async def fake_get(self, path):
        seen["path"] = path
        return {
            "id": "fake-task-1",
            "status": "succeeded",
            "model": "mureka-8",
            "choices": [{"url": "https://cdn.example.com/new.mp3", "duration": 90000, "id": "c9"}],
        }

    monkeypatch.setattr(MurekaProvider, "_get", fake_get)
    return seen


def _song_row(song_id: int) -> Song:
    db = SessionLocal()
    try:
        return db.get(Song, song_id)
    finally:
        db.close()


def _song_count(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(Song).filter(Song.user_id == user_id).count()
    finally:
        db.close()


def test_instrumental_endpoint(client, mureka_engine, monkeypatch):
    """POST /instrumental → /instrumental/generate，且轮询走独立 /instrumental/query。"""
    cap = _capture_post(monkeypatch)
    seen = _stub_poll(monkeypatch)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "inst@test.com")
    r = client.post(
        "/api/songs/instrumental", json={"prompt": "安静的钢琴", "title": "夜"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert cap["path"] == "/instrumental/generate"
    assert cap["json"]["prompt"] == "安静的钢琴"
    # 关键：不是 /song/query —— 复用会查不到任务（官方 instrumental 是独立查询端点）
    assert seen["path"] == "/instrumental/query/fake-task-1", seen

    row = _song_row(r.json()["song_id"])
    assert row.task_type == "instrumental"
    assert row.make_instrumental is True
    assert row.status == "completed"
    assert row.audio_url == "https://cdn.example.com/new.mp3"
    assert row.is_public is False  # 「发布必须后置」：创建时一律不公开


def test_easy_generate_endpoint(client, mureka_engine, monkeypatch):
    """POST /easy-generate → /song/easy-generate（prompt-only，无 lyrics），轮询 /song/query。"""
    cap = _capture_post(monkeypatch)
    seen = _stub_poll(monkeypatch)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "easy@test.com")
    r = client.post(
        "/api/songs/easy-generate",
        json={"prompt": "一首温暖的敬拜", "styles": ["gospel", "lo-fi"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert cap["path"] == "/song/easy-generate"
    assert cap["json"]["styles"] == ["gospel", "lo-fi"]
    assert "lyrics" not in cap["json"]
    assert seen["path"] == "/song/query/fake-task-1"

    row = _song_row(r.json()["song_id"])
    assert row.task_type == "easy"
    assert row.status == "completed"
    assert row.is_public is False


def test_region_edit_creates_child_and_keeps_parent(client, mureka_engine, monkeypatch):
    """局部改词产出**子曲**，原曲保留（与 extend/cover/remix 一致）。"""
    cap = _capture_post(monkeypatch)
    _stub_poll(monkeypatch)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "re@test.com")
    sid = _make_song("re@test.com")
    r = client.post(
        f"/api/songs/{sid}/region-edit",
        json={"lyrics": "新的这句", "edit_start": 30000, "edit_end": 50000},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert cap["path"] == "/song/region-edit"
    assert cap["json"]["song_id"] == "mock-custom-0001"  # 父曲的供应商 ID
    assert cap["json"]["edit_start"] == 30000

    child = _song_row(r.json()["song_id"])
    assert child.parent_song_id == sid
    assert child.task_type == "region_edit"
    assert child.lyric == "新的这句"

    parent = _song_row(sid)
    assert parent.status == "completed"  # 原曲未被改写/未被动
    assert parent.lyric == "orig lyric"


def test_region_edit_interval_too_short_400(client, mureka_engine):
    """官方硬约束：改写区间 ≥3000ms。路由层提前 400，别让它在后台任务里炸。"""
    h = _auth_headers(client, "re400@test.com")
    sid = _make_song("re400@test.com")
    r = client.post(
        f"/api/songs/{sid}/region-edit",
        json={"lyrics": "x", "edit_start": 0, "edit_end": 2000},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert "3000ms" in r.json()["detail"]


def test_stem_endpoint_is_sync_and_creates_no_song(client, mureka_engine, monkeypatch):
    """stem 是同步导出：直接返回 zip_url + expires_at，不建 Song、不轮询。"""

    async def fake_post(self, path, json=None):
        assert path == "/song/stem"
        assert json["model"] == "audio-separation-2"  # 默认模型
        return {
            "zip_url": "https://x/z.zip",
            "expires_at": 1700000000,
            "midi_zip_url": "https://x/m.zip",
        }

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)
    h = _auth_headers(client, "stem@test.com")
    sid = _make_song("stem@test.com")
    uid = _song_row(sid).user_id
    before = _song_count(uid)

    r = client.post(f"/api/songs/{sid}/stem", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["zip_url"] == "https://x/z.zip"
    assert body["expires_at"] == 1700000000
    assert body["midi_zip_url"] == "https://x/m.zip"
    # 导出≠生成：不应新增作品
    assert _song_count(uid) == before


def test_stem_rejects_unknown_model(client, mureka_engine):
    h = _auth_headers(client, "stem400@test.com")
    sid = _make_song("stem400@test.com")
    r = client.post(f"/api/songs/{sid}/stem?model=audio-separation-9", headers=h)
    assert r.status_code == 400, r.text


def test_vocal_clone_endpoint(client, mureka_engine, monkeypatch):
    """POST /vocal-clone：multipart 上传 → /vocal/clone，同步返回 vocal_id。"""
    captured = _capture_multipart(monkeypatch, response={"id": "vocal-1"})
    h = _auth_headers(client, "vc@test.com")
    r = client.post(
        "/api/songs/vocal-clone",
        files={"file": ("v.m4a", b"12345", "audio/mp4")},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["vocal_id"] == "vocal-1"
    assert captured["path"] == "/vocal/clone"
    assert "file" in captured["files"]


@pytest.mark.parametrize("filename", ["a.wav", "a.mp4", "noext"])
def test_vocal_clone_rejects_unsupported_format(client, mureka_engine, filename):
    """官方只收 mp3 / m4a —— 提前 400，别把 10MB 流量白烧在必然失败的请求上。"""
    h = _auth_headers(client, f"vc-{filename}@test.com")
    r = client.post(
        "/api/songs/vocal-clone",
        files={"file": (filename, b"12345", "application/octet-stream")},
        headers=h,
    )
    assert r.status_code == 400, r.text


def test_new_endpoints_fail_closed_on_mock(client):
    """MockProvider 未声明阶段 2 能力 → 全部 501（新端点同样继承 fail-closed）。"""
    h = _auth_headers(client, "newmock@test.com")
    sid = _make_song("newmock@test.com")
    assert (
        client.post("/api/songs/instrumental", json={"prompt": "x"}, headers=h).status_code == 501
    )
    assert (
        client.post("/api/songs/easy-generate", json={"prompt": "x"}, headers=h).status_code == 501
    )
    assert (
        client.post(
            f"/api/songs/{sid}/region-edit",
            json={"lyrics": "x", "edit_start": 0, "edit_end": 5000},
            headers=h,
        ).status_code
        == 501
    )
    assert client.post(f"/api/songs/{sid}/stem", headers=h).status_code == 501
    assert (
        client.post(
            "/api/songs/vocal-clone", files={"file": ("v.m4a", b"1", "audio/mp4")}, headers=h
        ).status_code
        == 501
    )


# ============================================================
# 阶段 2 细节锁定（本会话后端补全项）
# ============================================================
def test_easy_generate_rejects_unknown_style_400(client, mureka_engine):
    """easy-generate 仅接受官方风格枚举，非法值 → 400（而非静默忽略让用户以为生效）。"""
    h = _auth_headers(client, "easy-style@test.com")
    r = client.post(
        "/api/songs/easy-generate",
        json={"prompt": "一首温暖的歌", "styles": ["reggaeton", "gospel"]},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert "reggaeton" in r.json()["detail"]


def test_region_edit_rejects_nonpositive_interval_400(client, mureka_engine):
    """edit_end <= edit_start → 400（早于「区间 <3000ms」的更早一层守卫）。"""
    h = _auth_headers(client, "re-guard@test.com")
    sid = _make_song("re-guard@test.com")
    r = client.post(
        f"/api/songs/{sid}/region-edit",
        json={"lyrics": "x", "edit_start": 5000, "edit_end": 5000},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert "结束时间须大于开始时间" in r.json()["detail"]


def test_generate_chain_persists_and_injects_vocal_id(client, mureka_engine, monkeypatch):
    """音色克隆 ID 端到端：创建时落库 → 后台重建 GenerateRequest → 注入 Mureka 请求体（非 o2 模型）。"""
    cap = _capture_post(monkeypatch)
    _stub_poll(monkeypatch)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "vchain@test.com")
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "一首安静的敬拜诗歌", "lyric": "恩典够我用", "vocal_id": "vocal-abc123"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 1) 落库：Song.vocal_id 持久化
    row = _song_row(r.json()["song_id"])
    assert row.vocal_id == "vocal-abc123", row.vocal_id

    # 2) 注入：Mureka 请求体带上了 vocal_id（默认 mureka-9 ≠ o2）
    assert cap["path"] == "/song/generate", cap
    assert cap["json"].get("vocal_id") == "vocal-abc123", cap["json"]


def test_generate_vocal_id_stripped_for_o2(client, mureka_engine, monkeypatch):
    """mureka-o2 不支持 vocal_id：后端必须自动剔除（fail-closed，不把必然失败的字段发给供应商）。"""
    cap = _capture_post(monkeypatch)
    _stub_poll(monkeypatch)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "vo2@test.com")
    r = client.post(
        "/api/songs/generate",
        json={
            "prompt": "一首歌",
            "lyric": "x",
            "vocal_id": "vocal-o2test",
            "model_version": "mureka-o2",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "vocal_id" not in cap["json"], cap["json"]
    # 仍落到 Song.vocal_id（记录用户意图），只是不发给供应商
    assert _song_row(r.json()["song_id"]).vocal_id == "vocal-o2test"


def test_stem_expires_at_iso_returned(client, mureka_engine, monkeypatch):
    """stem 响应应附带 expires_at_iso（秒级时间戳换算的 ISO 字符串），便于前端直接展示。"""

    async def fake_post(self, path, json=None):
        return {
            "zip_url": "https://x/z.zip",
            "expires_at": 1700000000,
            "midi_zip_url": "https://x/m.zip",
        }

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)
    h = _auth_headers(client, "stemiso@test.com")
    sid = _make_song("stemiso@test.com")
    r = client.post(f"/api/songs/{sid}/stem", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    iso = body.get("expires_at_iso")
    assert isinstance(iso, str) and "T" in iso, body
    # 与 expires_at 同源：1700000000 → 2023-11-14T22:13:20+00:00
    assert iso.startswith("2023-11-14T22:13:20"), iso
