"""P2 Failover：引擎链拦截切换 + 明确审核提示（网络无关契约测试）。

- client.generate(engine_chain=[...]) 在一引擎拦截/失败时换下一引擎；
- resolve_engine_chain 按 路由首要→default→其余优先级 排序；
- 全部失败返回 failed + moderation_blocked + 原因。

注意：get_orchestrator 会把引擎名 .lower()，故映射键与链路均用小写。
"""

import asyncio

import pytest

from app.orchestrator.base import (
    GenerateRequest,
    ModerationBlockedError,
    MusicProvider,
    SongResult,
)
import app.orchestrator.client as client
from app.db import SessionLocal
from app.models import EngineConfig, RoutingRule
from app.orchestrator.selection import invalidate_config_cache, resolve_engine_chain


class _FakeProvider(MusicProvider):
    """可控假供应商：mode 决定 create/get 行为。"""

    def __init__(self, marker: str, mode: str = "ok"):
        self.marker = marker
        self.mode = mode  # ok | block_create | fail_poll | error_poll
        self.create_calls = 0
        self.get_calls = 0

    async def create(self, req: GenerateRequest) -> SongResult:
        self.create_calls += 1
        if self.mode == "block_create":
            raise ModerationBlockedError(f"blocked by {self.marker}")
        return SongResult(external_id=f"ext-{self.marker}", status="processing")

    async def get(self, external_id: str) -> SongResult:
        self.get_calls += 1
        if self.mode == "fail_poll":
            return SongResult(
                external_id=external_id,
                status="failed",
                moderation_note="content violates policy",
            )
        if self.mode == "error_poll":
            raise RuntimeError(f"boom {self.marker}")
        return SongResult(external_id=external_id, status="completed", audio_url=f"u-{self.marker}")


@pytest.fixture(autouse=True)
def _reset_override():
    # conftest 注入了全局 MockProvider 覆盖；本文件测试需绕过覆盖，用 engine_chain + 假 _build。
    # 关键：teardown 必须还原原始覆盖（conftest 的 MockProvider），否则会污染其后运行的测试。
    orig = client._provider_override
    client.set_orchestrator(None)
    client._providers.clear()
    yield
    client._providers.clear()
    client.set_orchestrator(orig)


def _chain_build(mapping: dict):
    def _b(name):
        if name not in mapping:
            # 复刻真实 _build：未注册引擎抛 ValueError（generate 捕获后跳过）
            raise ValueError(f"引擎 '{name}' 已注册但未接入 provider 实现")
        return mapping[name]

    return _b


def test_failover_block_create_tries_next(monkeypatch):
    a = _FakeProvider("a", "block_create")
    b = _FakeProvider("b", "ok")
    monkeypatch.setattr(client, "_build", _chain_build({"a": a, "b": b}))
    res = asyncio.run(client.generate(GenerateRequest(lyric="x"), engine_chain=["a", "b"]))
    assert res.status == "completed", res
    assert a.create_calls == 1 and b.create_calls == 1
    assert b.get_calls == 1


def test_failover_fail_poll_tries_next(monkeypatch):
    a = _FakeProvider("a", "fail_poll")
    b = _FakeProvider("b", "ok")
    monkeypatch.setattr(client, "_build", _chain_build({"a": a, "b": b}))
    res = asyncio.run(client.generate(GenerateRequest(lyric="x"), engine_chain=["a", "b"]))
    assert res.status == "completed", res
    assert a.get_calls >= 1  # 首引擎轮询到 failed 即切
    assert b.get_calls == 1


def test_failover_query_error_tries_next(monkeypatch):
    a = _FakeProvider("a", "error_poll")
    b = _FakeProvider("b", "ok")
    monkeypatch.setattr(client, "_build", _chain_build({"a": a, "b": b}))
    res = asyncio.run(client.generate(GenerateRequest(lyric="x"), engine_chain=["a", "b"]))
    assert res.status == "completed", res


def test_failover_exhausted_returns_failed_with_note(monkeypatch):
    a = _FakeProvider("a", "fail_poll")
    b = _FakeProvider("b", "fail_poll")
    monkeypatch.setattr(client, "_build", _chain_build({"a": a, "b": b}))
    res = asyncio.run(client.generate(GenerateRequest(lyric="x"), engine_chain=["a", "b"]))
    assert res.status == "failed"
    assert res.moderation_blocked is True
    assert "a" in (res.moderation_note or "") and "b" in res.moderation_note


def test_failover_unregistered_engine_skipped(monkeypatch):
    a = _FakeProvider("a", "ok")
    monkeypatch.setattr(client, "_build", _chain_build({"a": a}))
    # "ghost" 未注册（_build 抛 ValueError），应被跳过，最终由 a 完成
    res = asyncio.run(client.generate(GenerateRequest(lyric="x"), engine_chain=["ghost", "a"]))
    assert res.status == "completed", res
    assert a.create_calls == 1


def test_resolve_engine_chain_prioritizes_route():
    sl = SessionLocal()
    try:
        sl.query(RoutingRule).delete()
        sl.query(EngineConfig).delete()
        sl.add(
            EngineConfig(
                provider="mureka", display_name="M", enabled=True, is_default=False, priority=200
            )
        )
        sl.add(
            EngineConfig(
                provider="suno", display_name="S", enabled=True, is_default=True, priority=100
            )
        )
        sl.add(
            EngineConfig(
                provider="mock", display_name="K", enabled=True, is_default=False, priority=0
            )
        )
        sl.add(
            RoutingRule(category="christian_worship", provider="mureka", priority=200, enabled=True)
        )
        sl.commit()
        invalidate_config_cache()
        chain = resolve_engine_chain(sl, "christian_worship")
    finally:
        sl.close()
    assert chain[0] == "mureka", chain
    assert set(chain) == {"mureka", "suno", "mock"}, chain
    assert chain.index("mureka") < chain.index("suno") < chain.index("mock")
