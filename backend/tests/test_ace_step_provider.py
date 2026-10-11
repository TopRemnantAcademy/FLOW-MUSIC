"""ACE-Step 1.5 私有引擎 provider 契约测试（不依赖真实 GPU/网络）。

协议基线：CNB `chguanw/ace-step-1.5` 的 `acestep/api_server.py`（MIT, © 2026 ACEStudio），
经实取源码核对：
  · POST /release_task (L2253) → `_wrap_response({"task_id","status","queue_position"})`
  · POST /query_result  (L2467) → `_wrap_response([...])`
  · GET  /v1/audio?path= (L2809)
  · STATUS_MAP (L239)：0=queued|running / 1=succeeded / 2=failed
  · L2585 返回 `{"data": [...], "code":..., "error":...}` → provider 须剥掉 data 外层。

⚠ 本文件一律在 **HTTP 层** mock（httpx.MockTransport），不再 mock `_post`：
mock `_post` 会把「data 外层剥离」这段真实逻辑整个绕过，测出来的绿是假的。
"""

import asyncio
import json

import httpx
import pytest

from app.orchestrator import client as client_mod
from app.orchestrator import selection as selection_mod
from app.orchestrator.ace_step import (
    _INSTRUMENTAL_INSTRUCTION,
    AceStepError,
    AceStepPrivateProvider,
)
from app.orchestrator.base import GenerateRequest


def _make_provider() -> AceStepPrivateProvider:
    p = AceStepPrivateProvider.__new__(AceStepPrivateProvider)
    p.base = "http://127.0.0.1:8001"
    p.api_key = "test-key"
    p.model = "acestep-v15-turbo"
    p._sem = asyncio.Semaphore(1)
    p._timeout = None
    return p


# ---------- HTTP 层 mock 工具 ----------
def _wrap(data) -> dict:
    """复刻 ACE-Step `_wrap_response`（api_server.py:246）。"""
    return {"data": data, "code": 200, "error": None}


def _patch_http(monkeypatch, handler) -> None:
    """把 provider 内的 httpx 换成本地 handler，杜绝测试打到真实 127.0.0.1:8001。"""

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr("httpx.AsyncClient", _PatchedClient)


def _ok_audio_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/audio"
    assert "path" in request.url.params
    return httpx.Response(200, content=b"ID3fake-audio-bytes")


def _compose_handlers(*handlers):
    """按请求路径把多个 handler 组合成一个（ACE-Step 完成态会连续命中两条路由）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        for h in handlers:
            try:
                return h(request)
            except (AssertionError, LookupError):
                continue
        return httpx.Response(404, json={"detail": "no route in test stub"})

    return handler


# ---------- 注册与能力声明 ----------
def test_ace_step_registered_in_client_map():
    """selection 预留的 ace_step_private 必须真正接入 provider（填「已注册未接入」的坑）。"""
    assert "ace_step_private" in client_mod._PROVIDER_CLASSES
    prov = client_mod._build("ace_step_private")
    assert isinstance(prov, AceStepPrivateProvider)


def test_supported_tasks_is_honest():
    """只声明真正分流的 task；cover/remix 必须不在其中（避免假功能）。"""
    assert AceStepPrivateProvider.supported_tasks == frozenset({"generate", "custom"})


# ---------- 请求体构造 ----------
def test_payload_maps_generate_fields():
    p = _make_provider()
    req = GenerateRequest(prompt="worship", lyric="v1", style="piano", title="찬송")
    payload = p._build_payload(req)
    assert payload["task_type"] == "text2music"
    assert payload["lyrics"] == "v1"
    assert payload["audio_format"] == "mp3"
    assert "piano" in payload["prompt"]
    assert "worship" in payload["prompt"]
    # 未指定 model_version 时不得下发空串（避免服务端拿到 model="" 报错）
    assert "model" not in payload


def test_payload_instrumental_clears_lyrics_and_sets_instruction():
    p = _make_provider()
    payload = p._build_payload(GenerateRequest(prompt="ballad", lyric="v1", make_instrumental=True))
    assert payload["lyrics"] == ""
    assert payload["instruction"] == _INSTRUMENTAL_INSTRUCTION


# ---------- create（走真实 HTTP 链路）----------
def test_create_returns_processing_with_task_id(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json=_wrap({"task_id": "job-123", "status": "queued", "queue_position": 1})
        )

    _patch_http(monkeypatch, handler)
    p = _make_provider()
    res = asyncio.run(p.create(GenerateRequest(lyric="v1", prompt="worship")))
    assert res.status == "processing"
    assert res.external_id == "job-123"
    assert res.custom_id == "job-123"
    assert captured["method"] == "POST"
    assert captured["path"] == "/release_task"
    assert captured["body"]["lyrics"] == "v1"
    # 鉴权：api_key 存在时必须带 Bearer（api_server.py:299 的 header 回退分支）
    assert captured["auth"] == "Bearer test-key"


def test_create_raises_when_no_task_id(monkeypatch):
    _patch_http(monkeypatch, lambda r: httpx.Response(200, json=_wrap({})))
    p = _make_provider()
    with pytest.raises(AceStepError):
        asyncio.run(p.create(GenerateRequest(lyric="v1")))


def test_create_raises_on_401_from_server(monkeypatch):
    """服务端 401 必须直接失败（不可重试），且错误带处置建议。"""
    _patch_http(monkeypatch, lambda r: httpx.Response(401, json={"detail": "Invalid ai_token"}))
    p = _make_provider()
    with pytest.raises(AceStepError) as exc:
        asyncio.run(p.create(GenerateRequest(lyric="v1")))
    assert "ACE_STEP_API_KEY" in str(exc.value.remediation)


# ---------- get：状态映射（走真实 HTTP 链路）----------
def _query_handler(status: int, result: str) -> "object":
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query_result"
        body = json.loads(request.content.decode("utf-8"))
        item = {
            "task_id": body["task_id_list"][0],
            "status": status,
            "result": result,
            "progress_text": "",
        }
        return httpx.Response(200, json=_wrap([item]))

    return handler


def test_get_running_returns_processing(monkeypatch):
    _patch_http(monkeypatch, _query_handler(0, "[]"))
    p = _make_provider()
    res = asyncio.run(p.get("job-1"))
    assert res.status == "processing"


def test_get_failed_returns_failed_with_note(monkeypatch):
    result = json.dumps(
        [{"file": "", "error": "CUDA out of memory", "metas": {}}], ensure_ascii=False
    )
    _patch_http(monkeypatch, _query_handler(2, result))
    p = _make_provider()
    res = asyncio.run(p.get("job-1"))
    assert res.status == "failed"
    assert "CUDA" in (res.moderation_note or "")


def test_get_succeeded_without_file_fails_closed(monkeypatch):
    """已完成但无音频文件 → 不得「假装成功」返回空 audio_url。"""
    result = json.dumps([{"file": "", "metas": {}}])
    _patch_http(monkeypatch, _query_handler(1, result))
    p = _make_provider()
    res = asyncio.run(p.get("job-1"))
    assert res.status == "failed"
    assert "音频文件" in (res.moderation_note or "")


def test_get_succeeded_publishes_audio(monkeypatch):
    result = json.dumps(
        [
            {
                "file": "/tmp/acestep/out.mp3",
                "lyrics": "v1",
                "metas": {
                    "bpm": 96,
                    "duration": 183.2,
                    "genres": "gospel",
                    "keyscale": "C",
                    "timesignature": "4/4",
                },
            }
        ],
        ensure_ascii=False,
    )
    # 成功态还会触发音频回源 GET /v1/audio，handler 须同时覆盖两条路由。
    _patch_http(monkeypatch, _compose_handlers(_query_handler(1, result), _ok_audio_handler))
    monkeypatch.setattr("app.storage.s3.is_configured", lambda: True)
    captured: dict = {}

    def fake_upload_bytes(key, data, content_type="application/octet-stream"):
        captured["key"] = key
        captured["bytes"] = len(data)
        return "https://cdn.example.com/" + key

    monkeypatch.setattr("app.storage.s3.upload_bytes", fake_upload_bytes)
    p = _make_provider()
    res = asyncio.run(p.get("job-1"))
    assert captured["key"] == "ace_step/job-1.mp3"
    assert captured["bytes"] > 0
    assert res.status == "completed"
    assert res.audio_url == "https://cdn.example.com/ace_step/job-1.mp3"
    assert res.lyric == "v1"
    assert res.duration == 183  # float → int 秒


# ---------- 音频回源与发布 ----------
def test_fetch_and_publish_rejects_when_storage_unconfigured(monkeypatch):
    """对象存储未配置 ⇒ 音频无法播放 ⇒ 必须明确报错，而不是返回空 URL 让播放静默失败。"""
    _patch_http(monkeypatch, _ok_audio_handler)
    p = _make_provider()
    monkeypatch.setattr("app.storage.s3.is_configured", lambda: False)
    with pytest.raises(AceStepError) as exc:
        asyncio.run(p._fetch_and_publish("/tmp/acestep/out.mp3", "job-1"))
    assert "对象存储未配置" in str(exc.value)


def test_fetch_and_publish_uploads_when_storage_configured(monkeypatch):
    _patch_http(monkeypatch, _ok_audio_handler)
    p = _make_provider()
    monkeypatch.setattr("app.storage.s3.is_configured", lambda: True)
    captured: dict = {}

    def fake_upload_bytes(key, data, content_type="application/octet-stream"):
        captured["key"] = key
        captured["bytes"] = len(data)
        return "https://cdn.example.com/" + key

    monkeypatch.setattr("app.storage.s3.upload_bytes", fake_upload_bytes)
    url = asyncio.run(p._fetch_and_publish("/tmp/acestep/out.mp3", "job-1"))
    assert captured["key"] == "ace_step/job-1.mp3"
    assert captured["bytes"] > 0
    assert url.endswith("ace_step/job-1.mp3")


def test_fetch_and_publish_reports_http_error(monkeypatch):
    _patch_http(monkeypatch, lambda r: httpx.Response(404, json={"detail": "Audio file not found"}))
    p = _make_provider()
    with pytest.raises(AceStepError) as exc:
        asyncio.run(p._fetch_and_publish("/tmp/nope.mp3", "job-1"))
    assert "回源" in str(exc.value)


# ---------- 引擎注册表一致性 ----------
def test_default_engine_registry_has_ace_step():
    """selection 的种子引擎必须都能被 client 构造出来（防止再次出现「已注册未接入」）。"""
    names = {e["provider"] for e in selection_mod.DEFAULT_ENGINES}
    assert "ace_step_private" in names
    for name in names:
        if name in client_mod._PROVIDER_CLASSES:
            client_mod._build(name)
