"""阶段 2 · Mureka 全流程 mock 端到端测试（**有状态**假 Mureka 服务器）。

与 test_stage0_capability_gate.py 的「单响应打桩」不同，本文件用一个**有状态**的假
Mureka 服务器模拟真实任务生命周期（提交 → queued/running → succeeded/failed），
从而真正覆盖：
  · 多轮轮询循环（不是首轮即完成）
  · 引擎专属轮询端点（instrumental 走 /instrumental/query，song 走 /song/query）
  · 结果字段回写（duration ms→s、custom_id、lyrics_sync、external_id）
  · vocal_id 是否真正上线（wire 级断言）
  · region-edit 的父子血缘与供应商 song_id
  · 失败 / 轮询超时的写回（moderation_note 可读原因）
  · 音色克隆的 multipart 路径与扩展名校验

⚠️ 关键约束：settings.song_poll_interval **必须为正数**。
   client.generate 的轮询循环直接用该值累加 waited（client.py:92-94），若为 0 则
   waited 恒为 0 → while 永不退出 = 死循环（仅当首轮即 completed 才侥幸不触发）。
   故本文件用 0.05s，让「多轮轮询」成为可被真实触发与断言的行为。

所有网络调用均被 monkeypatch 拦截，**不发起任何真实请求**。
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.orchestrator.client import set_orchestrator
from app.orchestrator.mock import MockProvider
from app.orchestrator.mureka import MurekaProvider


# ============================================================
# 有状态假 Mureka 服务器
# ============================================================
class FakeMurekaServer:
    """模拟 Mureka HTTP 层的任务生命周期，并录制每一次出站请求（wire log）。

    - succeed_after：第 N 次轮询才返回 succeeded（默认 2 → 真实覆盖「多轮轮询」）。
    - fail_tasks：所有任务在第 1 次轮询即返回 failed + failed_reason。
    """

    # 提交类端点 → 任务类型（song 走 /song/query，instrumental 走 /instrumental/query）
    SUBMIT_PATHS = {
        "/song/generate": "song",
        "/song/extend": "song",
        "/song/remix": "song",
        "/song/easy-generate": "song",
        "/song/region-edit": "song",
        "/instrumental/generate": "instrumental",
    }

    def __init__(
        self,
        succeed_after: int = 2,
        fail_tasks: bool = False,
        fail_reason: str = "内容违规：检测到受限内容",
    ):
        self.succeed_after = succeed_after
        self.fail_tasks = fail_tasks
        self.fail_reason = fail_reason
        self.tasks: dict[str, dict] = {}
        self.calls: list[tuple] = []  # (method, path, payload)
        self._seq = 0

    # ---- 出站请求处理 ----
    def handle_post(self, path: str, payload: dict) -> dict:
        self.calls.append(("POST", path, payload))
        if path == "/song/stem":
            # 同步接口：直接返回分轨包（含秒级 expires_at），无 task_id。
            return {
                "zip_url": "https://fake-cdn/z.zip",
                "expires_at": 1700000000,  # 2023-11-14T22:13:20Z
                "midi_zip_url": "https://fake-cdn/m.zip",
            }
        kind = self.SUBMIT_PATHS.get(path)
        if kind is None:
            raise AssertionError(f"假 Mureka：意外 POST 路径 {path}")
        self._seq += 1
        tid = f"task-{self._seq:04d}"
        self.tasks[tid] = {"polls": 0, "kind": kind, "model": payload.get("model")}
        return {
            "id": tid,
            "created_at": 1700000000,
            "model": payload.get("model"),
            "status": "queued",
        }

    def handle_multipart(self, path: str, data: dict, files: dict) -> dict:
        self.calls.append(("POST-MP", path, {"data": data, "files": sorted(files)}))
        if path == "/vocal/clone":
            return {
                "id": "vocal-fake-0001",
                "filename": "v.mp3",
                "bytes": 10,
                "created_at": 1700000000,
            }
        if path == "/files/upload":
            return {"id": "file-fake-0001"}
        raise AssertionError(f"假 Mureka：意外 multipart 路径 {path}")

    def handle_get(self, path: str) -> dict:
        self.calls.append(("GET", path, None))
        if path == "/account/billing":
            return {
                "account_id": "acct-fake",
                "balance": 976,
                "total_recharge": 1000,
                "total_spending": 23,
                "concurrent_request_limit": 1,
            }
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[1] == "query" and parts[0] in ("song", "instrumental"):
            tid = parts[2]
            kind = parts[0]
        else:
            raise AssertionError(f"假 Mureka：意外 GET 路径 {path}")
        t = self.tasks.get(tid)
        if not t:
            return {"id": tid, "status": "failed", "failed_reason": "task not found"}
        t["polls"] += 1
        if self.fail_tasks:
            return {"id": tid, "status": "failed", "failed_reason": self.fail_reason}
        if t["polls"] < self.succeed_after:
            return {"id": tid, "status": "running", "model": t["model"]}
        return self._succeeded_body(tid, t, kind)

    def _succeeded_body(self, tid: str, t: dict, kind: str) -> dict:
        # choices[] 采用**真机实测**字段集：url / flac_url / wav_url / id / duration(ms) /
        # lyrics_sections；**刻意不含** title/cover_url/lyrics（官方实测确实没有）→
        # 用来验证「标题保留用户值、封面为空、歌词走 lyrics_sections」。
        return {
            "id": tid,
            "status": "succeeded",
            "model": t["model"] or "mureka-9",
            "choices": [
                {
                    "url": f"https://cdn.fake/{tid}.mp3",
                    "flac_url": f"https://cdn.fake/{tid}.flac",
                    "wav_url": f"https://cdn.fake/{tid}.wav",
                    "id": f"clip-{tid}",
                    "duration": 156710,  # 毫秒 → 期望秒=157
                    "lyrics_sections": [
                        {
                            "section_type": "verse",
                            "start": 0,
                            "end": 8000,
                            "lines": [
                                {
                                    "start": 0,
                                    "end": 4000,
                                    "text": "line one",
                                    "words": [{"start": 0, "end": 500, "text": "line"}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    # ---- 断言辅助 ----
    def posts_to(self, path: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]

    def gets_to_prefix(self, prefix: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "GET" and c[1].startswith(prefix)]

    def multipart_to(self, path: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "POST-MP" and c[1] == path]


# ============================================================
# 夹具与辅助
# ============================================================
@pytest.fixture()
def mureka_engine():
    """临时把编排层切到真实 MurekaProvider（网络一律被下面的 install 打桩）。"""
    set_orchestrator(MurekaProvider())
    yield
    set_orchestrator(MockProvider())  # 还原 conftest 注入的 mock


def install_fake_mureka(monkeypatch, server: FakeMurekaServer) -> None:
    async def fake_post(self, path, json=None):
        return server.handle_post(path, json or {})

    async def fake_post_multipart(self, path, data=None, files=None):
        return server.handle_multipart(path, data or {}, files or {})

    async def fake_get(self, path):
        return server.handle_get(path)

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)
    monkeypatch.setattr(MurekaProvider, "_post_multipart", fake_post_multipart)
    monkeypatch.setattr(MurekaProvider, "_get", fake_get)


def _fast_poll(monkeypatch, timeout: float = 5.0) -> None:
    """加速后台轮询；用**正数** interval（见模块 docstring 的死循环说明）。"""
    monkeypatch.setattr(settings, "song_poll_interval", 0.05)
    monkeypatch.setattr(settings, "song_poll_timeout", timeout)
    monkeypatch.setattr(settings, "enable_preview_clip", False)
    monkeypatch.setattr(settings, "enable_audio_postprocess", False)
    monkeypatch.setattr(settings, "enable_audio_moderation", False)


def _auth_headers(client: TestClient, email: str) -> dict:
    pw = "Password123!"
    client.post("/api/auth/register", json={"email": email, "password": pw})
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_song(email: str, lyric: str = "orig lyric") -> int:
    db = SessionLocal()
    try:
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
        return s.id
    finally:
        db.close()


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


# ============================================================
# ① 全流程：音色克隆 → 普通创作（带 vocal_id）→ 完成 + 字段回写
# ============================================================
def test_mock_full_journey_clone_then_generate(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=2)
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "journey@test.com")

    # 1) 音色克隆（multipart，同步返回 vocal_id）
    r = client.post(
        "/api/songs/vocal-clone", files={"file": ("v.m4a", b"12345", "audio/mp4")}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["vocal_id"] == "vocal-fake-0001"
    assert server.multipart_to("/vocal/clone"), server.calls

    # 2) 普通创作（带上克隆出的 vocal_id）
    r = client.post(
        "/api/songs/generate",
        json={
            "prompt": "一首安静的敬拜诗歌",
            "lyric": "恩典够我用",
            "title": "夜之歌",
            "vocal_id": "vocal-fake-0001",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    song_id = r.json()["song_id"]

    # 3) 结果落库 + 字段回写
    row = _song_row(song_id)
    assert row.status == "completed", row.status
    assert re.match(r"https://cdn\.fake/task-\d+\.mp3", row.audio_url or ""), row.audio_url
    assert (row.custom_id or "").startswith("clip-task-"), row.custom_id
    assert row.duration == 157, row.duration  # 156710ms → 157s
    assert row.lyrics_sync and "section_type" in row.lyrics_sync  # 逐字时间轴已落库
    assert row.title == "夜之歌"  # choices 无 title → 保留用户值
    assert row.moderation_status == "approved"  # enable_moderation 下自动通过
    assert row.external_id and row.external_id.startswith("task-")

    # 4) wire 级断言：vocal_id 真正上线；且确实多轮轮询（不是首轮即完成）
    gen = server.posts_to("/song/generate")
    assert len(gen) == 1, server.calls
    assert gen[0][2]["vocal_id"] == "vocal-fake-0001", gen[0][2]
    assert gen[0][2]["lyrics"] == "恩典够我用"
    polls = server.gets_to_prefix("/song/query/")
    assert len(polls) >= 2, f"应多轮轮询，实际 {len(polls)} 次"


# ============================================================
# ② 纯器乐：独立轮询端点 + 无 lyrics 字段
# ============================================================
def test_mock_instrumental_uses_separate_query(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=3)
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "inst-mock@test.com")
    r = client.post(
        "/api/songs/instrumental", json={"prompt": "安静的钢琴独奏", "title": "夜"}, headers=h
    )
    assert r.status_code == 200, r.text

    row = _song_row(r.json()["song_id"])
    assert row.status == "completed"
    assert row.task_type == "instrumental"
    assert row.make_instrumental is True

    # 端点形状：/instrumental/generate，且**无** lyrics 字段
    assert server.posts_to("/instrumental/generate"), server.calls
    assert "lyrics" not in server.posts_to("/instrumental/generate")[0][2]
    # 轮询必须走独立端点，且**绝不**复用 /song/query
    assert len(server.gets_to_prefix("/instrumental/query/")) >= 3
    assert server.gets_to_prefix("/song/query/") == [], server.calls


# ============================================================
# ③ 灵感创作：官方 styles 枚举 + vocal_id，走 /song/query
# ============================================================
def test_mock_easy_generate_styles_and_vocal(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=2)
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "easy-mock@test.com")
    r = client.post(
        "/api/songs/easy-generate",
        json={
            "prompt": "一首温暖的歌",
            "styles": ["gospel", "lo-fi"],
            "title": "暖",
            "vocal_id": "vocal-fake-0001",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text

    row = _song_row(r.json()["song_id"])
    assert row.status == "completed"
    assert row.task_type == "easy"
    assert row.style == "gospel, lo-fi"  # 路由把 styles 落进 style 供展示

    payload = server.posts_to("/song/easy-generate")[0][2]
    assert payload["styles"] == ["gospel", "lo-fi"]
    assert "lyrics" not in payload  # easy-generate 官方无 lyrics 字段
    assert payload["vocal_id"] == "vocal-fake-0001"
    assert server.gets_to_prefix("/song/query/"), server.calls


# ============================================================
# ④ 局部改词：子曲血缘 + 供应商 song_id + 原曲保留
# ============================================================
def test_mock_region_edit_child_lineage(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=2)
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "re-mock@test.com")
    parent_id = _make_song("re-mock@test.com")

    r = client.post(
        f"/api/songs/{parent_id}/region-edit",
        json={
            "lyrics": "新的这句",
            "edit_start": 30000,
            "edit_end": 50000,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    child_id = r.json()["song_id"]
    assert child_id != parent_id

    # wire：用**父曲的供应商 ID** 作为 song_id，区间原样（ms）
    payload = server.posts_to("/song/region-edit")[0][2]
    assert payload["song_id"] == "mock-custom-0001", payload
    assert payload["edit_start"] == 30000 and payload["edit_end"] == 50000
    assert payload["lyrics"] == "新的这句"

    child = _song_row(child_id)
    assert child.parent_song_id == parent_id
    assert child.task_type == "region_edit"
    assert child.status == "completed"
    assert child.lyric == "新的这句"

    parent = _song_row(parent_id)
    assert parent.status == "completed" and parent.lyric == "orig lyric"  # 原曲未被动


# ============================================================
# ⑤ 分轨导出：同步接口 → zip + expires_at_iso，且不建 Song、不轮询
# ============================================================
def test_mock_stem_sync_zip_and_iso(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer()
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "stem-mock@test.com")
    sid = _make_song("stem-mock@test.com")
    uid = _song_row(sid).user_id
    before = _song_count(uid)

    r = client.post(f"/api/songs/{sid}/stem", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["zip_url"] == "https://fake-cdn/z.zip"
    assert body["midi_zip_url"] == "https://fake-cdn/m.zip"
    assert body["expires_at"] == 1700000000
    # 路由把秒级时间戳换算为 ISO（1700000000 → 2023-11-14T22:13:20+00:00）
    assert (body.get("expires_at_iso") or "").startswith("2023-11-14T22:13:20"), body

    # 导出 ≠ 生成：不新增作品，也不轮询
    assert _song_count(uid) == before
    assert server.gets_to_prefix("/song/query/") == []
    assert server.gets_to_prefix("/instrumental/query/") == []
    # model 默认 audio-separation-2 已传给供应商
    assert server.posts_to("/song/stem")[0][2]["model"] == "audio-separation-2"


# ============================================================
# ⑥ 音色克隆：扩展名校验（mp3/m4a 通过，其余 400）且路径为 /vocal/clone
# ============================================================
def test_mock_vocal_clone_extension_guard(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer()
    install_fake_mureka(monkeypatch, server)

    h = _auth_headers(client, "vc-mock@test.com")
    ok = client.post(
        "/api/songs/vocal-clone", files={"file": ("voice.mp3", b"abc", "audio/mpeg")}, headers=h
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["vocal_id"] == "vocal-fake-0001"
    # 真实路径是 /vocal/clone（不是 /song/vocal-clone）
    assert server.multipart_to("/vocal/clone"), server.calls

    bad = client.post(
        "/api/songs/vocal-clone", files={"file": ("voice.wav", b"abc", "audio/wav")}, headers=h
    )
    assert bad.status_code == 400, bad.text
    # 非法扩展名在**读文件前**被拦，不应产生任何出站请求
    assert len(server.multipart_to("/vocal/clone")) == 1, server.calls


# ============================================================
# ⑦ 失败写回：供应商 failed → 作品 failed 且留下可读原因
# ============================================================
def test_mock_failed_task_writes_reason(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(fail_tasks=True, fail_reason="内容违规：检测到受限内容")
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "fail-mock@test.com")
    r = client.post("/api/songs/instrumental", json={"prompt": "x"}, headers=h)
    assert r.status_code == 200, r.text

    row = _song_row(r.json()["song_id"])
    assert row.status == "failed", row.status
    assert "内容违规" in (row.moderation_note or ""), row.moderation_note


# ============================================================
# ⑧ 轮询超时：永不完成 → failed 且原因为「轮询超时」
# ============================================================
def test_mock_poll_timeout_marks_failed(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=99999)  # 永不 succeeded
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch, timeout=0.3)

    h = _auth_headers(client, "timeout-mock@test.com")
    r = client.post("/api/songs/instrumental", json={"prompt": "x"}, headers=h)
    assert r.status_code == 200, r.text

    row = _song_row(r.json()["song_id"])
    assert row.status == "failed", row.status
    assert "轮询超时" in (row.moderation_note or ""), row.moderation_note


# ============================================================
# ⑨ mureka-o2：wire 上必须剔除 vocal_id（但仍落库用户意图）
# ============================================================
def test_mock_o2_strips_vocal_id_on_wire(client, mureka_engine, monkeypatch):
    server = FakeMurekaServer(succeed_after=1)
    install_fake_mureka(monkeypatch, server)
    _fast_poll(monkeypatch)

    h = _auth_headers(client, "o2-mock@test.com")
    r = client.post(
        "/api/songs/generate",
        json={
            "prompt": "一首歌",
            "lyric": "x",
            "vocal_id": "vocal-o2",
            "model_version": "mureka-o2",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text

    payload = server.posts_to("/song/generate")[0][2]
    assert "vocal_id" not in payload, payload  # o2 不支持 → 必须剔除
    assert payload["model"] == "mureka-o2"
    assert _song_row(r.json()["song_id"]).vocal_id == "vocal-o2"  # 用户意图仍落库
