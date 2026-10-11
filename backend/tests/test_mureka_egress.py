"""Mureka 出口自检（preflight）回归测试。

覆盖目标（P0-CRIT）：把「403 地域封锁」从**用户生成失败后才发现**变成
**部署完第一行日志就可见**。全部用例打桩 `MurekaProvider.get_billing`，不发真实网络请求。
"""

import asyncio

import pytest

from app.config import settings
from app.mureka_health import (
    DEFAULT_PREFLIGHT_TIMEOUT,
    format_report,
    probe_egress,
    run_startup_preflight,
)
from app.orchestrator.mureka import MurekaError, MurekaProvider


def _fake_billing():
    """正常账单响应（2026-09-12 实弹探测真值形状）。"""

    async def fake(self):
        return {
            "account_id": 79327450365953,
            "balance": 976,
            "total_recharge": 1000,
            "total_spending": 23,
            "concurrent_request_limit": 1,
        }

    return fake


def test_probe_egress_ok(monkeypatch):
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")
    monkeypatch.setattr(MurekaProvider, "get_billing", _fake_billing())
    r = asyncio.run(probe_egress())
    assert r["ok"] is True
    assert r["kind"] == "ok"
    assert r["status_code"] == 200
    assert r["concurrent_request_limit"] == 1
    assert r["billing"]["balance"] == 976


def test_probe_egress_403_is_region_block(monkeypatch):
    """403 必须判为地域封锁，并带出「中国香港部署」的处置建议。"""
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")

    async def fake(self):
        raise MurekaError(
            403,
            "region",
            "Mureka GET /account/billing 失败 403 [region]: region not allowed",
            remediation="该部署出口疑似被 Mureka 地域封锁(403)。请在中国香港等允许区域部署",
        )

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(probe_egress())
    assert r["ok"] is False
    assert r["kind"] == "region"
    assert r["status_code"] == 403
    assert "中国香港" in r["remediation"]


def test_probe_egress_401_is_auth(monkeypatch):
    monkeypatch.setattr(settings, "mureka_api_key", "bad-key")

    async def fake(self):
        raise MurekaError(401, "auth", "Invalid Authentication", remediation="检查 MUREKA_API_KEY")

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(probe_egress())
    assert r["ok"] is False
    assert r["kind"] == "auth"
    assert r["status_code"] == 401


def test_probe_egress_network_error(monkeypatch):
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")

    async def fake(self):
        raise OSError("dns failure")

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(probe_egress())
    assert r["ok"] is False
    assert r["kind"] == "network"
    assert r["status_code"] is None


def test_probe_egress_timeout_becomes_network(monkeypatch):
    """超时必须收敛为 network，且不能让启动期卡住（provider read=180s 太长）。"""
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")

    async def fake(self):
        await asyncio.sleep(5)

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(probe_egress(timeout=0.05))
    assert r["kind"] == "network"
    assert r["status_code"] is None


def test_probe_egress_disabled_without_key_and_no_network(monkeypatch):
    """未配 key 时直接判 disabled，且**不发起任何网络请求**。"""
    monkeypatch.setattr(settings, "mureka_api_key", "")
    calls = {"n": 0}

    async def fake(self):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(probe_egress())
    assert r["kind"] == "disabled"
    assert r["ok"] is False
    assert calls["n"] == 0


def test_startup_preflight_off_skips(monkeypatch):
    monkeypatch.setattr(settings, "mureka_preflight_mode", "off")
    assert asyncio.run(run_startup_preflight()) is None


def test_startup_preflight_warn_does_not_block_startup(monkeypatch):
    """默认 warn：地域封锁只告警，绝不阻断启动（避免 crashloop）。"""
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")
    monkeypatch.setattr(settings, "mureka_preflight_mode", "warn")

    async def fake(self):
        raise MurekaError(403, "region", "blocked", remediation="换区域部署")

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    r = asyncio.run(run_startup_preflight())
    assert r["kind"] == "region"


def test_startup_preflight_strict_refuses_startup_on_region(monkeypatch):
    monkeypatch.setattr(settings, "mureka_api_key", "test-key")
    monkeypatch.setattr(settings, "mureka_preflight_mode", "strict")

    async def fake(self):
        raise MurekaError(403, "region", "blocked", remediation="换区域部署")

    monkeypatch.setattr(MurekaProvider, "get_billing", fake)
    with pytest.raises(RuntimeError):
        asyncio.run(run_startup_preflight())


def test_format_report_includes_kind_status_and_action():
    s = format_report(
        {
            "kind": "region",
            "status_code": 403,
            "message": "blocked",
            "remediation": "换区域部署",
        }
    )
    assert "region" in s
    assert "403" in s
    assert "换区域部署" in s


def test_preflight_timeout_must_stay_short():
    """启动期探测超时必须远小于 provider 的 read=180s。

    否则出口不可达时会把服务启动卡死数分钟（曾经 180s read timeout 的教训）。
    """
    assert DEFAULT_PREFLIGHT_TIMEOUT <= 15
