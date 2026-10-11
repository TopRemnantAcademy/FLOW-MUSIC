"""Mureka **出口自检**（preflight）——把「地域封锁」从事后失败变成部署即见。

背景（P0-CRIT）：
    Mureka 仅允许特定区域（如中国香港）访问，非允许区域出口会收到 **403**。
    在 `orchestrator/mureka.py` 里我们已经把 403 分类为 `kind="region"` 并给出处置建议，
    但那是**用户提交生成失败之后**才能看到——排查成本高、且早期会误判为随机故障。

本模块做的事：
    用**免费**的 `GET /v1/account/billing`（不计费、不消耗额度）在**启动期**探一次出口，
    把结果结构化（ok / region / auth / param / rate_limit / server / network / disabled），
    由 `main.py` lifespan 直接打日志 → **部署完成的第一行日志就能判定出口是否可用**。

    ⚠️ 本机（开发环境）探测到 200 **不代表**生产出口可用 —— 真正的结论必须在
    **目标部署机（中国香港）**上跑 `backend/scripts/probe_mureka.py` 才能得到。
    本模块的价值是让那一次探测的结果**立刻可见且可判读**，而不是等用户来报障。

设计约束：
- **永不抛异常**（探测失败也只返回结构化结果），启动期探测绝不能拖垮/阻断服务；
- **短超时**（默认 8s）：provider 的 read timeout 是 180s（生成轮询需要），
  启动期不能用它——否则出口不可达时会把启动卡住 3 分钟。
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.orchestrator.mureka import MurekaError, MurekaProvider

log = logging.getLogger(__name__)

#: 启动期探测超时（秒）。刻意远小于 provider 的 read=180s。
DEFAULT_PREFLIGHT_TIMEOUT = 8.0

#: 各 kind 的日志级别与面向运维的一句话结论。
#:   error  = 必须人工处置，核心功能（生成）当前不可用
#:   warning= 环境/临时性问题，可能自愈
#:   info   = 正常或未配置
_KIND_LOG_LEVEL = {
    "ok": "info",
    "disabled": "info",
    "region": "error",
    "auth": "error",
    "param": "error",
    "rate_limit": "warning",
    "server": "warning",
    "network": "warning",
    "unknown": "warning",
}


async def probe_egress(timeout: float = DEFAULT_PREFLIGHT_TIMEOUT) -> dict:
    """探测当前部署出口到 Mureka 的连通性/授权/地域，**永不抛异常**。

    返回结构（稳定契约，前端/脚本可直接消费）：
      ok              bool   —— 出口可用且 key 有效
      kind            str    —— ok | region | auth | param | rate_limit | server | network | unknown | disabled
      status_code     int?   —— HTTP 状态码（网络层失败为 None）
      message         str    —— 结论/原始错误
      remediation     str    —— 处置建议（可直接展示）
      billing         dict?  —— ok 时的账单快照
      concurrent_request_limit int? —— 账号并发上限（= 权威的 MUREKA_MAX_CONCURRENT）
    """
    if not (settings.mureka_api_key or "").strip():
        return {
            "ok": False,
            "kind": "disabled",
            "status_code": None,
            "message": "未配置 MUREKA_API_KEY —— 生成功能不可用（探测跳过）",
            "remediation": "在环境变量中配置 MUREKA_API_KEY 后重启",
            "billing": None,
            "concurrent_request_limit": None,
        }

    provider = MurekaProvider()
    try:
        # wait_for 保证启动期不会卡住（provider 自带 read=180s，对探测太长）
        billing = await asyncio.wait_for(provider.get_billing(), timeout=timeout)
    except MurekaError as e:
        # 已分类（401 认证 / 403 地域封锁 / 400 参数 / 429 限流 / 5xx 服务端）
        return {
            "ok": False,
            "kind": e.kind,
            "status_code": e.status_code,
            "message": str(e),
            "remediation": e.remediation,
            "billing": None,
            "concurrent_request_limit": None,
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "kind": "network",
            "status_code": None,
            "message": f"出口探测超时（>{timeout}s）",
            "remediation": "检查到 api.mureka.ai 的出口连通性/代理设置",
            "billing": None,
            "concurrent_request_limit": None,
        }
    except Exception as e:  # 网络层/解析等未预期异常 → 一律视为 network
        return {
            "ok": False,
            "kind": "network",
            "status_code": None,
            "message": f"{type(e).__name__}: {e}",
            "remediation": "检查到 api.mureka.ai 的出口连通性/代理设置",
            "billing": None,
            "concurrent_request_limit": None,
        }

    return {
        "ok": True,
        "kind": "ok",
        "status_code": 200,
        "message": "出口可用且 API Key 有效",
        "remediation": "",
        "billing": billing,
        "concurrent_request_limit": billing.get("concurrent_request_limit"),
    }


def format_report(result: dict) -> str:
    """把探测结果格式化为一行人类可读的结论（脚本/日志共用）。"""
    kind = result.get("kind", "unknown")
    sc = result.get("status_code")
    base = f"Mureka 出口自检: kind={kind}"
    if sc is not None:
        base += f" status={sc}"
    base += f" —— {result.get('message', '')}"
    rem = result.get("remediation") or ""
    if rem:
        base += f" | 处置: {rem}"
    return base


async def run_startup_preflight() -> dict | None:
    """启动期自检入口（main.py lifespan 调用）。

    模式（settings.mureka_preflight_mode）：
      off    —— 不探测（离线/CI 用）
      warn   —— 默认，仅按级别打日志，**不阻断启动**
      strict —— region/auth 这类「必须人工处置」的结论直接 RuntimeError 拒绝启动
                （用于希望地理封锁立刻暴露、宁可不起服务的部署）
    """
    mode = (settings.mureka_preflight_mode or "warn").strip().lower()
    if mode == "off":
        return None

    result = await probe_egress()
    line = format_report(result)
    level = _KIND_LOG_LEVEL.get(result.get("kind", "unknown"), "warning")
    getattr(log, level, log.warning)(line)

    # 并发上限与本地配置不一致时提示（权威值来自账号，避免手填错档位）
    limit = result.get("concurrent_request_limit")
    if result.get("ok") and isinstance(limit, int) and limit > 0:
        if limit != settings.mureka_max_concurrent:
            log.warning(
                "MUREKA_MAX_CONCURRENT=%s 与账号实际并发上限 %s 不一致，建议对齐",
                settings.mureka_max_concurrent,
                limit,
            )

    if mode == "strict" and result.get("kind") in ("region", "auth"):
        raise RuntimeError(f"[MUREKA_PREFLIGHT_STRICT] {line}")
    return result
