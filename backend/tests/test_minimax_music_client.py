"""MiniMax Music 3 客户端契约测试（不依赖真实网络，mock httpx 与配置）。

协议基线：https://platform.minimax.io/docs/api-reference/music-generation
          https://platform.minimax.cn/docs/api-reference/music-generation
核对日期：2026-09-26
覆盖：请求体组装与官方字段约束、RPM 限速间隔、响应解析（hex/url/业务码）、
      缺 key 与 HTTP 错误的响亮失败（不静默跳过）。
"""

import asyncio
import json
import time

import httpx
import pytest
from app.orchestrator.minimax_music import (
    DISCONTINUATION_NOTICE,
    MAX_LYRICS_CHARS,
    MAX_PROMPT_CHARS,
    MiniMaxMusicClient,
    MiniMaxMusicError,
    _RpmPacer,
    describe_specs,
)

_PARSE = MiniMaxMusicClient.parse_response  # parse_response 是类上的 @staticmethod


def _client(**kw) -> MiniMaxMusicClient:
    """构造客户端：绕过 settings 依赖，只注入关心的字段。"""
    kw.setdefault("api_key", "test-key")
    kw.setdefault("base", "https://api.minimax.io/v1")
    kw.setdefault("model", "music-3.0")
    kw.setdefault("rpm", 60)  # 缩短测试间隔
    kw.setdefault("timeout", 5.0)
    return MiniMaxMusicClient(**kw)


# —— 请求体 ——


def test_payload_follows_official_contract():
    """官方必填字段齐备：model/prompt/lyrics/stream/output_format/audio_setting。"""
    p = _client().build_payload(prompt=" worship ballad", lyrics="[Verse]\nline")
    assert p["model"] == "music-3.0"
    assert p["prompt"] == "worship ballad"
    assert p["lyrics"].startswith("[Verse]")
    assert p["stream"] is False
    assert p["output_format"] == "hex"  # url 24h 过期，默认取 hex
    assert p["audio_setting"]["sample_rate"] == 44100


def test_aigc_watermark_is_explicitly_false():
    """官方文档条目 aigc_watermark（仅在 stream=false 生效）：显式下发 False，
    避免服务端默认开或改默认值时悄悄污染 A/B 样本。"""
    p = _client().build_payload(prompt="worship", lyrics="[Verse]\nline")
    assert p["aigc_watermark"] is False
    assert (
        _client().build_payload(prompt="worship", lyrics="[Verse]\nline", aigc_watermark=True)[
            "aigc_watermark"
        ]
        is True
    )


def test_unknown_model_is_rejected():
    """不在官方 model 枚举内的一律报错（不猜、不兜底）。"""
    with pytest.raises(ValueError):
        _client(model="music-9.0").build_payload(prompt="x", lyrics="[Verse]\n")


def test_prompt_is_optional_when_lyrics_are_supplied():
    """官方：非纯音乐 + 自填歌词时 prompt 允许 0 字符（[0,2000]）。"""
    p = _client().build_payload(prompt="", lyrics="[Verse]\nline")
    assert p["prompt"] == ""
    # 纯音乐 / 无歌词且不开 optimizer → prompt 必填
    with pytest.raises(ValueError):
        _client().build_payload(prompt="", lyrics="")


def test_lyrics_optimizer_allows_empty_lyrics():
    """官方：lyrics_optimizer=true 且 lyrics 为空时，由 prompt 自动生成歌词。"""
    p = _client().build_payload(prompt="worship ballad", lyrics_optimizer=True)
    assert "lyrics" not in p
    assert p["lyrics_optimizer"] is True
    assert p["prompt"]


def test_audio_setting_values_are_whitelisted():
    """audio_setting 候选值不在白名单时报错（丢过去只会换来 2013）。"""
    ok = _client().build_payload(
        prompt="w", lyrics="[Verse]\n", audio_setting={"sample_rate": 24000, "format": "wav"}
    )
    assert ok["audio_setting"] == {"sample_rate": 24000, "bitrate": 256000, "format": "wav"}
    for bad in ({"sample_rate": 48000}, {"bitrate": 999999}, {"format": "flac"}):
        with pytest.raises(ValueError):
            _client().build_payload(prompt="w", lyrics="[Verse]\n", audio_setting=bad)


def test_stream_requires_hex_output():
    """官方约束：stream=true 时 output_format 只能是 hex。"""
    with pytest.raises(ValueError):
        _client().build_payload(prompt="w", lyrics="[Verse]\n", stream=True, output_format="url")


def test_cover_model_rules():
    """music-cover 官方规格：不支持 is_instrumental / lyrics_optimizer，
    prompt 必填 10-300，cover 三字段只能给一个。"""
    base = {"prompt": "Smooth jazz lounge", "lyrics": ""}
    with pytest.raises(ValueError):
        _client(model="music-cover").build_payload(
            **base, is_instrumental=True, audio_url="https://x.invalid/a.mp3"
        )
    with pytest.raises(ValueError):
        _client(model="music-cover").build_payload(
            **base, lyrics_optimizer=True, audio_url="https://x.invalid/a.mp3"
        )
    with pytest.raises(ValueError):  # 必填 10-300
        _client(model="music-cover").build_payload(prompt="ab", audio_url="https://x.invalid/a.mp3")
    with pytest.raises(ValueError):  # 三者互斥
        _client(model="music-cover").build_payload(
            prompt="Smooth jazz lounge",
            audio_url="https://x.invalid/a.mp3",
            audio_base64="AAAA",
        )
    p = _client(model="music-cover").build_payload(
        prompt="Smooth jazz lounge", audio_url="https://x.invalid/a.mp3"
    )
    assert p["audio_url"] == "https://x.invalid/a.mp3"
    assert "lyrics" not in p  # 官方：不传则 ASR 抽取


def test_cover_fields_rejected_on_text_models():
    """audio_url / audio_base64 / cover_feature_id 仅 cover 模型可用。"""
    with pytest.raises(ValueError):
        _client().build_payload(prompt="w", lyrics="[Verse]\n", audio_url="https://x.invalid/a.mp3")


def test_instrumental_makes_lyrics_optional_but_prompt_required():
    """官方：is_instrumental=true 时 lyrics 非必填，但 prompt 必填。"""
    ok = _client().build_payload(prompt="cinematic pad", is_instrumental=True)
    assert "lyrics" not in ok and ok["is_instrumental"] is True
    with pytest.raises(ValueError):
        _client().build_payload(prompt="", lyrics="", is_instrumental=True)


def test_oversized_fields_raise_instead_of_silent_truncation():
    """越界字段直接报错（不做截断），避免「以为传了、实际没传」。"""
    with pytest.raises(ValueError):
        _client().build_payload(prompt="x" * (MAX_PROMPT_CHARS + 1), lyrics="[Verse]\n")
    with pytest.raises(ValueError):
        _client().build_payload(prompt="worship", lyrics="x" * (MAX_LYRICS_CHARS + 1))
    with pytest.raises(ValueError):
        _client().build_payload(prompt="worship", lyrics="")  # 非纯音乐时 lyrics 必填


def test_instrumental_rejects_lyrics():
    """官方：is_instrumental=true 是纯音乐，再传 lyrics 属于自相矛盾。"""
    with pytest.raises(ValueError):
        _client().build_payload(prompt="cinematic pad", is_instrumental=True, lyrics="[Verse]\n")


# —— 限速 ——


def test_rpm_pacer_interval_matches_official_rpm():
    """官方 free 档 RPM=3 → 相邻调用间隔 20s。"""
    assert _RpmPacer(3).interval == pytest.approx(20.0)
    assert _RpmPacer(0).interval == 60.0  # rpm<=0 兜底为 1/min


def test_rpm_pacer_actually_sleeps():
    """真睡（不是只算间隔）：两次 acquire 之间应至少经过一个 interval。"""

    async def run() -> float:
        pacer = _RpmPacer(600)  # interval 0.1s
        start = time.monotonic()
        await pacer.acquire()
        await pacer.acquire()
        return time.monotonic() - start

    assert asyncio.run(run()) >= 0.09


# —— 响应解析 ——


def test_parse_response_decodes_hex_and_maps_extra_info():
    body = {
        "base_resp": {"status_code": 0, "status_msg": "success"},
        "trace_id": "04ede0ab",
        "extra_info": {
            "music_duration": 25364,
            "music_sample_rate": 44100,
            "music_channel": 2,
            "bitrate": 256000,
            "music_size": 813651,
        },
        "data": {"audio": "aabbcc", "status": 2},
    }
    r = _PARSE(body)
    assert r.audio_bytes == bytes.fromhex("aabbcc")
    assert r.duration_ms == 25364
    assert r.sample_rate == 44100 and r.channels == 2 and r.bitrate == 256000
    assert r.trace_id == "04ede0ab"


def test_parse_response_accepts_url_format():
    body = {
        "base_resp": {"status_code": 0},
        "data": {"audio": "https://example.invalid/a.mp3"},
    }
    r = _PARSE(body)
    assert r.audio_url == "https://example.invalid/a.mp3"
    assert r.audio_bytes is None


def test_parse_response_raises_when_data_status_is_not_complete():
    """官方 200 示例里 data.status == 2；其他值按「疑似未完成」处理，不当成功。"""
    body = {
        "base_resp": {"status_code": 0, "status_msg": "success"},
        "trace_id": "t2",
        "data": {"audio": "aabb", "status": 1},
    }
    with pytest.raises(MiniMaxMusicError) as ei:
        _PARSE(body)
    assert "data.status=1" in str(ei.value)
    assert ei.value.trace_id == "t2"
    assert _PARSE({**body, "data": {"audio": "aabb", "status": 2}}).data_status == 2


def test_parse_response_raises_on_business_error_code():
    body = {
        "base_resp": {"status_code": 2049, "status_msg": "Insufficient balance"},
        "trace_id": "t1",
        "data": {},
    }
    with pytest.raises(MiniMaxMusicError) as ei:
        _PARSE(body)
    assert ei.value.kind == "business"
    assert ei.value.status_msg == "Insufficient balance"
    assert ei.value.trace_id == "t1"
    assert "2049" in str(ei.value)


def test_describe_specs_mirrors_official_model_enum():
    """UI 能力开关直接由官方规格表推导，6 个 model 一个不能少。"""
    rows = {r["model"]: r for r in describe_specs()}
    assert set(rows) == {
        "music-3.0",
        "music-2.6",
        "music-cover",
        "music-3.0-free",
        "music-2.6-free",
        "music-cover-free",
    }
    assert rows["music-3.0"]["rpm"] == 120 and rows["music-3.0"]["discontinued"] is False
    assert rows["music-3.0-free"]["rpm"] == 3 and rows["music-3.0-free"]["discontinued"] is True
    assert rows["music-cover"]["cover"] is True
    assert rows["music-cover"]["is_instrumental"] is False


# —— 失败必须响亮 ——


def test_missing_api_key_raises_loudly():
    c = _client(api_key="")
    with pytest.raises(MiniMaxMusicError) as ei:
        c._headers()
    assert ei.value.kind == "auth"
    assert "MINIMAX_API_KEY" in str(ei.value)
    # music-3.0 未停服 → 不带停服公告（不带错误信息误导运维）
    assert DISCONTINUATION_NOTICE not in str(ei.value)


def test_missing_api_key_on_discontinued_model_carries_notice():
    c = _client(api_key="", model="music-3.0-free")
    with pytest.raises(MiniMaxMusicError) as ei:
        c._headers()
    assert DISCONTINUATION_NOTICE in str(ei.value)


def _transport_client(handler, **ckw) -> httpx.AsyncClient:
    """用 MockTransport 构造注入型 AsyncClient（不依赖 pytest-asyncio / monkeypatch）。"""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **ckw)


def test_http_401_is_classified_as_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api key"})

        c = _client(client=_transport_client(handler))
        with pytest.raises(MiniMaxMusicError) as ei:
            asyncio.run(c.generate(prompt="worship", lyrics="[Verse]\nline"))
        assert ei.value.kind == "auth"
        # music-3.0 未停服 → 401 就是「key 无效/无权限」，不带停服公告
        assert DISCONTINUATION_NOTICE not in str(ei.value)


def test_free_tier_notice_is_appended_on_auth_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    c = _client(model="music-2.6-free", client=_transport_client(handler))
    with pytest.raises(MiniMaxMusicError) as ei:
        asyncio.run(c.generate(prompt="worship", lyrics="[Verse]\nline"))
    assert "停止服务" in str(ei.value)


def test_generate_success_path_returns_result():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "trace_id": "trace-1",
                "extra_info": {"music_duration": 120000, "music_sample_rate": 44100},
                "data": {"audio": "deadbeef", "status": 2},
            },
        )

    c = _client(client=_transport_client(handler))
    result = asyncio.run(c.generate(prompt="worship", lyrics="[Verse]\nline"))
    assert result.audio_bytes == bytes.fromhex("deadbeef")
    assert result.duration_ms == 120000
    req = captured[0]
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.url.path == "/v1/music_generation"
    # 解析 JSON 比较语义，不锁死分隔符字面量（json.dumps 的空格与否是实现细节）
    sent = json.loads(req.content)
    assert sent["model"] == "music-3.0"
    assert sent["prompt"] == "worship"
    assert sent["lyrics"] == "[Verse]\nline"
