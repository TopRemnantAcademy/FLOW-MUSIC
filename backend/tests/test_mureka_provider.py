"""Mureka provider 契约测试（不依赖真实网络，mock _post/_get）。

覆盖：prompt 由用户输入原样组合（**曲风不再强制注入**，2026-09-10 起）+ 声线；
create 提交体字段；get 对 succeeded/failed/processing 的映射；
错误包体抽取与 429 双义重试判定（platform.mureka.ai/docs/en/error-codes.html，2026-09-03 抓取）。
请求/响应字段以 platform.mureka.ai 官方文档（2026-09-03 抓取）为准。
"""

import asyncio

import httpx
import pytest

from app.orchestrator.base import GenerateRequest
from app.orchestrator.mureka import MurekaError, MurekaProvider, _raise_mureka_error


def _fake_resp(status: int, json_body: dict) -> httpx.Response:
    return httpx.Response(status_code=status, json=json_body)


def _make_provider() -> MurekaProvider:
    p = MurekaProvider.__new__(MurekaProvider)
    p.base = "https://api.mureka.ai/v1"
    p.api_key = "test-key"
    p.model = "mureka-9"
    p.n = 1
    p._sem = asyncio.Semaphore(1)
    p._timeout = None
    return p


def test_build_prompt_passes_through_user_input_only():
    """[2026-09-10] gospel 강제 주입 제거 후: 사용자 입력만 그대로 조합된다."""
    p = _make_provider()
    req = GenerateRequest(lyric="x", style="worship, piano", vocal_gender="female")
    prompt = p._build_prompt(req)
    assert "female" in prompt
    assert "worship" in prompt
    # 세속 주제도 gospel 로 오염되지 않는다 (구현이 parts=["gospel"] 로 시작하던 시절 회귀 방지)
    assert "gospel" not in prompt


def test_build_prompt_no_forced_gospel_for_secular_topic():
    """보편(세속) 주제를 넣어도 gospel 이 붙지 않아야 한다 — 제품 정위 결정 반영."""
    p = _make_provider()
    req = GenerateRequest(lyric="x", style="", prompt="失恋的雨夜")
    assert p._build_prompt(req) == "失恋的雨夜"


def test_create_submits_lyrics_model_and_gender():
    p = _make_provider()
    captured: dict = {}

    async def fake_post(path, json):
        captured["path"] = path
        captured["json"] = json
        return {"id": "1436211", "status": "preparing", "model": "mureka-9", "trace_id": "t1"}

    p._post = fake_post
    req = GenerateRequest(lyric="v1", style="piano", vocal_gender="male")
    res = asyncio.run(p.create(req))
    assert res.status == "processing"
    assert res.external_id == "1436211"
    assert res.model_version == "mureka-9"
    assert captured["path"] == "/song/generate"
    assert captured["json"]["lyrics"] == "v1"
    assert captured["json"]["model"] == "mureka-9"
    assert captured["json"]["n"] == 1
    # [2026-09-10] 강제 주입 제거 → 사용자가 넣은 스타일만 실린다
    assert captured["json"]["prompt"] == "piano, male"
    assert captured["json"]["gender"] == "male"


def test_create_omits_prompt_when_no_style_given():
    """스타일·주제·음색이 모두 비면 prompt 키 자체를 싣지 않는다(빈 문자열 전송 방지)."""
    p = _make_provider()
    captured: dict = {}

    async def fake_post(path, json):
        captured["json"] = json
        return {"id": "1436211", "status": "preparing", "model": "mureka-9"}

    p._post = fake_post
    req = GenerateRequest(lyric="v1")
    asyncio.run(p.create(req))
    assert "prompt" not in captured["json"]
    assert captured["json"]["lyrics"] == "v1"


def test_get_maps_succeeded_choice():
    p = _make_provider()

    async def fake_get(path):
        return {
            "id": "1",
            "status": "succeeded",
            "model": "mureka-9",
            "choices": [
                {
                    "audio_url": "http://a.mp3",
                    "cover_url": "http://c.png",
                    "lyrics": "ly",
                    "title": "T",
                    "duration": 120,
                    "id": "clip1",
                }
            ],
        }

    p._get = fake_get
    res = asyncio.run(p.get("1"))
    assert res.status == "completed"
    assert res.audio_url == "http://a.mp3"
    assert res.cover_url == "http://c.png"
    assert res.lyric == "ly"
    assert res.title == "T"
    assert res.duration == 120
    assert res.model_version == "mureka-9"
    assert res.custom_id == "clip1"


def test_get_maps_failed():
    p = _make_provider()

    async def fake_get(path):
        return {"id": "1", "status": "failed", "failed_reason": "content rejected"}

    p._get = fake_get
    res = asyncio.run(p.get("1"))
    assert res.status == "failed"
    # 失败原因作为审核提示落库（P2），不再误写入 lyric
    assert res.moderation_note == "content rejected"
    assert res.lyric is None


def test_get_maps_processing():
    p = _make_provider()

    async def fake_get(path):
        return {"id": "1", "status": "running"}

    p._get = fake_get
    res = asyncio.run(p.get("1"))
    assert res.status == "processing"


def test_extract_error_pulls_message_from_envelope():
    p = _make_provider()
    resp = _fake_resp(
        429, {"error": {"message": "You exceeded your current quota"}, "trace_id": "t"}
    )
    assert p._extract_error(resp) == "You exceeded your current quota"


def test_should_retry_quota_exhausted_is_false():
    # error-codes 文档：429 额度耗尽不可重试（等也白等）
    p = _make_provider()
    resp = _fake_resp(
        429,
        {
            "error": {
                "message": "You exceeded your current quota, please check your plan and billing details"
            }
        },
    )
    assert p._should_retry(resp) is False


def test_should_retry_rate_limit_is_true():
    # error-codes 文档：429 限流(Rate limit reached) 可退避重试
    p = _make_provider()
    resp = _fake_resp(429, {"error": {"message": "Rate limit reached, please retry later"}})
    assert p._should_retry(resp) is True


def test_should_retry_5xx_is_true():
    p = _make_provider()
    resp = _fake_resp(503, {"error": {"message": "engine overloaded"}})
    assert p._should_retry(resp) is True


def test_should_retry_non_retryable_status_is_false():
    p = _make_provider()
    resp = _fake_resp(401, {"error": {"message": "Invalid Authentication"}})
    assert p._should_retry(resp) is False


def test_create_does_not_send_title():
    # 官方 generate 请求体无 title 字段（歌名由 choices[].title 回传），锁定删除
    p = _make_provider()
    captured: dict = {}

    async def fake_post(path, json):
        captured["json"] = json
        return {"id": "1436211", "status": "preparing", "model": "mureka-9", "trace_id": "t1"}

    p._post = fake_post
    req = GenerateRequest(lyric="v1", style="piano", vocal_gender="male")
    asyncio.run(p.create(req))
    assert "title" not in captured["json"]


def test_create_truncates_prompt_to_1024():
    # 官方约束 prompt ≤1024（post-v1-song-generate.html）；超长截断避免 400
    p = _make_provider()
    captured: dict = {}

    async def fake_post(path, json):
        captured["json"] = json
        return {"id": "1436211", "status": "preparing", "model": "mureka-9", "trace_id": "t1"}

    p._post = fake_post
    long_prompt = "x" * 2000
    req = GenerateRequest(lyric="v1", style=long_prompt, vocal_gender="male")
    asyncio.run(p.create(req))
    assert len(captured["json"]["prompt"]) <= 1024
    # [2026-09-10] 강제 주입 제거 → 선두는 gospel 이 아니라 **사용자 스타일** 이다
    assert captured["json"]["prompt"].startswith("x")


def test_get_billing_parses_fields():
    # GET /v1/account/billing（2026-09-03 核对）：查余额/并发上限，提前诊断 429 quota
    p = _make_provider()

    async def fake_get(path):
        return {
            "account_id": 123,
            "balance": 0,
            "total_recharge": 20000,
            "total_spending": 20000,
            "concurrent_request_limit": 1,
        }

    p._get = fake_get
    billing = asyncio.run(p.get_billing())
    assert billing["balance"] == 0
    assert billing["total_recharge"] == 20000
    assert billing["concurrent_request_limit"] == 1
    # 余额 0 → 即我们曾遇到的 "You exceeded your current quota" 根因


# —— 403 地域封锁分类（P0-CRIT：香港部署前须确认，原 401/403 不可区分）——
def test_mureka_error_classifies_403_region_block():
    """403 必须被分类为 region（地域封锁），并给出「中国香港部署/出口代理」的处置建议。"""
    p = _make_provider()
    resp = _fake_resp(403, {"error": {"message": "region not allowed"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("POST", "/song/generate", resp)
    e = exc.value
    assert e.status_code == 403
    assert e.kind == "region"
    assert "地域封锁" in str(e)
    assert "中国香港" in str(e)


def test_mureka_error_classifies_403_region_from_forbidden_keyword():
    """即使服务端只回 'Forbidden' 不带 region 字样，403 也应按地域封锁提示（非认证错误）。"""
    p = _make_provider()
    resp = _fake_resp(403, {"error": {"message": "Forbidden"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("GET", "/song/query/x", resp)
    assert exc.value.kind == "region"


def test_mureka_error_classifies_401_auth():
    p = _make_provider()
    resp = _fake_resp(401, {"error": {"message": "Invalid Authentication"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("POST", "/song/generate", resp)
    assert exc.value.kind == "auth"


def test_mureka_error_classifies_400_param():
    p = _make_provider()
    resp = _fake_resp(400, {"error": {"message": "model mureka-o2 not allowed for this endpoint"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("GET", "/song/query/x", resp)
    assert exc.value.kind == "param"


def test_mureka_error_classifies_429_quota_rate_limit():
    p = _make_provider()
    resp = _fake_resp(429, {"error": {"message": "You exceeded your current quota"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("POST", "/song/generate", resp)
    assert exc.value.kind == "rate_limit"


def test_mureka_error_is_runtimeerror_backward_compatible():
    """既有 admin 账单健康检查 / billing 路由用 except RuntimeError 兜底，必须仍可捕获。"""
    p = _make_provider()
    resp = _fake_resp(403, {"error": {"message": "region not allowed"}})
    with pytest.raises(RuntimeError):
        _raise_mureka_error("POST", "/song/generate", resp)


def test_mureka_error_exposes_status_and_kind_for_programmatic_use():
    """上层（如 songs.py）可借 .kind 区分「地域封锁」与「密钥失效」，给出不同呈现。"""
    p = _make_provider()
    resp = _fake_resp(403, {"error": {"message": "region not allowed"}})
    with pytest.raises(MurekaError) as exc:
        _raise_mureka_error("POST", "/song/generate", resp)
    e = exc.value
    assert isinstance(e, RuntimeError)
    assert e.status_code == 403
    assert e.kind == "region"
