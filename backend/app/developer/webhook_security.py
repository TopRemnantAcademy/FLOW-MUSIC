"""Webhook URL 的 SSRF 校验。

为什么不复用 `app/security/ssrf.py::SSRFGuard`，而是另写一个**更严**的版本？
  1. `SSRFGuard` 允许注入 allowlist，并且默认会 **实际解析 DNS**。注册端点收的是
     用户随手填的 URL，解析失败（域名还不存在、内网域名）必须**立即拒绝**而不是
     "解析不到 → 放行或报 500"。本模块对解析失败一律拒绝（fail-closed）。
  2. 注册期**不能**做 DNS 解析并因此接受一个「当前解析到公网」的域名 ——
     那只是把问题推迟。真正必须挡住的是**字面量**（127.0.0.1 / 10.x / ::1）
     与非 http(s) scheme，这些无需 DNS 即可判定。域名指向的核查留给投递期
     （投递期做 IP 固定 + 握手前复验，见 delivery.py）。
  3. 这里要返回**人话原因**（"拒绝内网地址 10.0.0.5"），而 SSRFGuard 只抛
     统一 ValueError，无法直接作为 400 的 detail。

两道防线（都必需，不是重复劳动）：
  · 注册期（本模块）：挡住显式字面量与非法 scheme，攻击面收敛。
  · 投递期（`SSRFGuard` + IP 固定）：挡住 DNS 在注册之后改指向内网（DNS-rebinding / TOCTOU）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# 即便 DNS 解析成功也不放行的特殊主机名。裸的 `localhost` 会被 getaddrinfo 解析成
# 127.0.0.1，靠 IP 判定也能拦 —— 但显式列出可以给出更准确的原因，且不依赖解析器行为
# （某些环境把 localhost 映射到 ::1，IPv6 分支必须也能覆盖）。
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
    }
)

# 云元数据服务的**固定**地址：169.254.169.254（AWS/GCP/Azure/OpenStack 都用它）。
# 它落在 link-local 段内本已被 is_link_local 拦下，单独列出是为了让测试/日志
# 能一眼看出"这条规则是为云元数据设的"，而不是碰巧被别的规则命中。
CLOUD_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        # IPv4-mapped IPv6（::ffff:127.0.0.1）单看 is_loopback 是 False ——
        # 这是 SSRF 过滤最经典的绕过，必须显式展开再判一次。
        or (getattr(ip, "ipv4_mapped", None) is not None and _is_blocked_ip(ip.ipv4_mapped))
    )


def check_webhook_url(url: str) -> str:
    """校验通过返回规范化后的 url；否则抛 `ValueError`（调用方转 400）。

    检查顺序（从便宜到昂贵，也是从"能给出最准确原因"到"兜底"）：
      1. 非空、可解析
      2. scheme ∈ {http, https}（`file://` / `gopher://` / `ftp://` 全在这里被拒）
      3. 主机名存在、非 blocked 字面量
      4. 主机名是 IP 字面量 → 直接判（不走 DNS，确定性最高）
      5. 否则解析 DNS，**每一个**返回地址都必须是公网地址
         （只查第一个是漏洞：多 A 记录里混一个 10.0.0.5 就能绕过）
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("webhook URL 不能为空")

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(
            f"webhook URL 只支持 http/https（收到 {parsed.scheme or '空'}://）"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("webhook URL 缺少主机名")
    if host in BLOCKED_HOSTNAMES:
        raise ValueError(f"webhook URL 不允许指向本机/内网主机名: {host}")

    # 字面量 IP：直接判定，不碰 DNS。
    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError(f"webhook URL 不允许指向内网/回环地址: {host}")
        return raw

    # 域名：解析并逐个校验（fail-closed —— 解析失败即拒绝，不"先放行以后再查"）。
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except Exception as e:
        raise ValueError(f"webhook URL 主机无法解析: {host}（{type(e).__name__}）")
    resolved = []
    for info in infos:
        addr_str = info[4][0]
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            raise ValueError(f"webhook URL 解析出非法地址: {addr_str}")
        resolved.append(addr)
    if not resolved:
        raise ValueError(f"webhook URL 主机无可用地址: {host}")
    for addr in resolved:
        if _is_blocked_ip(addr):
            raise ValueError(f"webhook URL 解析到内网/回环地址: {host} → {addr}")
    return raw
