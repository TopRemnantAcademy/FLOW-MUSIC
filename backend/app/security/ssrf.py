"""음악 플랫폼 SSRF 가드 (자체 완결 — FLOW ``backend/app/security/ssrf.py`` 와 동일 설계).

용도:
  - ``routers/flow.py::_ensure_cdn_url`` 가 ``song.audio_url``(사용자 영향 값) 을
    서버 측 fetch 하기 전 내부/링크로컬/클라우드 메타데이터 주소 인출을 차단.
  - DNS-rebinding(TOCTOU) 방지를 위해 가드 통과 호스트를 한 번만 해석해 검증된 IP 로
    직접 연결(safe_ip_for + 호출처 핀닝).

음악 repo 는 FLOW 와 별개 ``app`` 패키지라 FLOW 의 ``ssrf.py`` 를 import 할 수 없으므로
동일 로직을 자체 모듈로 복사·유지한다(단일 소스 불일치 리스크는 문서로 고정).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


class SSRFGuard:
    def __init__(self, allow_private: bool = False, allowlist: set[str] | None = None):
        self.allow_private = allow_private
        self.allowlist = set(allowlist or set())

    def _resolved_ips(self, host: str) -> list[str]:
        try:
            return [info[4][0] for info in socket.getaddrinfo(host, None)]
        except Exception:
            return []

    def is_safe(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in _ALLOWED_SCHEMES:
                return False
            host = (parsed.hostname or "").lower()
            if not host:
                return False
            if host in self.allowlist:
                return True
            if self.allow_private:
                return True
            ips = self._resolved_ips(host)
            if not ips:
                return False
            for ip_str in ips:
                try:
                    addr = ipaddress.ip_address(ip_str)
                except ValueError:
                    return False
                if (
                    addr.is_loopback
                    or addr.is_private
                    or addr.is_link_local
                    or addr.is_reserved
                    or addr.is_multicast
                ):
                    return False
            return True
        except Exception:
            return False

    def guard(self, url: str) -> str:
        """안전하지 않으면 ValueError, 안전하면 url 반환."""
        if not self.is_safe(url):
            raise ValueError(f"SSRF guard: URL not allowed: {url}")
        return url

    def pin(self, url: str) -> tuple[str, str]:
        """가드 1회 + DNS 1회 해석 → ``(pinned_url, 원본 host)``.

        ``pinned_url`` 은 netloc 이 검증된 공인 IP 로 고정된 URL 이다. 요청 시 반드시
        ``Host: <원본 host>`` 헤더와 ``extensions={"sni_hostname": <원본 host>}`` 를 함께
        넘겨야 TLS SNI / 가상호스트가 깨지지 않는다.

        ⚠ httpx 0.27 계열은 ``AsyncHTTPTransport(server_name=...)`` 를 지원하지 않는다
        (0.27.2 실측: ``__init__`` 시그니처에 없음 → TypeError). IP 핀닝은 httpcore 의
        요청 단위 ``sni_hostname`` extension 으로만 가능하다
        (httpcore 1.0.9 ``_async/connection.py:107``, ``_sync/connection.py:107``).
        """
        self.guard(url)
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        safe_ip = self.safe_ip_for(host)
        netloc = f"[{safe_ip}]:{port}" if ":" in safe_ip else f"{safe_ip}:{port}"
        return parsed._replace(netloc=netloc).geturl(), host

    def safe_ip_for(self, host: str) -> str:
        """호스트를 한 번 해석해 검증된 안전 IP 1개 반환(DNS-rebinding 방지용 핀닝).

        없으면 ValueError(가드를 이미 통과한 호스트여도 재검증 — 방어적).
        """
        for ip_str in self._resolved_ips(host):
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if not (
                addr.is_loopback
                or addr.is_private
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
            ):
                return ip_str
        raise ValueError(f"SSRF guard: no safe IP for {host}")

    @contextlib.contextmanager
    def guarded_get_stream(self, url: str, timeout: float = 30, max_hops: int = 5):
        """同步流式 GET，**逐跳重新校验**重定向（防 302 绕过 / CWE-918）。

        背景：原调用点是 ``guard(url)`` 一次 + ``httpx.get(..., follow_redirects=True)``，
        重定向目标从未二次校验 → 公网 URL 302 到 169.254.169.254 即可打到云元数据。
        这里改为不交给 httpx 自动跟随，而是自己逐跳 ``guard`` 后再发下一跳。

        用法（响应体在 with 内消费，退出即关闭）::

            with ssrf_guard.guarded_get_stream(url, timeout=30) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(65536):
                    ...

        超出 max_hops 或重定向缺失 Location 时抛 ValueError。
        """
        current = url
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _ in range(max_hops):
                # 매 홉 guard + IP 핀ning(DNS-rebinding TOCTOU 차단)
                pinned, host = self.pin(current)
                req = client.build_request("GET", pinned, headers={"Host": host})
                resp = client.send(req, stream=True, extensions={"sni_hostname": host})
                if resp.status_code in _REDIRECT_STATUS:
                    loc = resp.headers.get("location")
                    resp.close()
                    if not loc:
                        raise ValueError(f"SSRF guard: redirect without Location: {current}")
                    current = urljoin(current, loc)
                    continue
                try:
                    yield resp
                finally:
                    resp.close()
                return
            raise ValueError(f"SSRF guard: too many redirects for {url}")

    async def safe_fetch(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60,
        **kwargs,
    ) -> httpx.Response:
        """httpx 래퍼 — 가드 + IP 핀닝 후 GET. DNS-rebinding(TOCTOU) 차단.

        ``client`` 를 넘기면 커넥션을 재사용한다(재시도 루프·대량 호출에서 권장 —
        호출마다 새 AsyncClient 를 만들면 DNS/TLS 핸드셰이크 폭풍이 발생한다).

        ⚠ 종전 구현은 ``httpx.AsyncHTTPTransport(server_name=host)`` 를 썼는데,
        httpx 0.27.2 에는 그런 파라미터가 없어 **호출 시 TypeError 로 죽는 죽은 코드**였다.
        지금은 ``pin()`` + 요청 단위 ``sni_hostname`` extension 으로 교체했다.
        """
        pinned, host = self.pin(url)
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Host", host)
        extensions = {"sni_hostname": host}
        extensions.update(kwargs.pop("extensions", None) or {})
        if client is not None:
            r = await client.get(
                pinned, timeout=timeout, headers=headers, extensions=extensions, **kwargs
            )
        else:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(
                    pinned, timeout=timeout, headers=headers, extensions=extensions, **kwargs
                )
        r.raise_for_status()
        return r


ssrf_guard = SSRFGuard()
