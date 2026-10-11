"""D34 / H4 / H5 回归：服务端出网抓取的 SSRF 闸门。

- D34: ``download_to_temp`` (app/storage/s3.py) 必须先用 SSRF 守卫检查
  ``song.audio_url``（用户影响值），与 routers/flow.py 同一标准。
- H4 : 重定向目标必须**逐跳重新校验**（公网 URL 302 → 169.254.169.254 曾经可绕过）。
- H5 : 校验通过后不得再由 httpx 重新解析 DNS（TOCTOU / DNS-rebinding）→ IP 固定。

IP 字面量 / 假解析均为本地确定性构造，无需网络。
"""

import asyncio
import contextlib

import pytest

from app.security.ssrf import SSRFGuard, ssrf_guard
from app.storage.s3 import download_to_temp


class _FakeResp:
    content = b"audio-bytes"

    def raise_for_status(self):
        return None


class _StreamResp:
    """download_to_temp 의 스트리밍 경로가 소비하는 최소 응답."""

    def raise_for_status(self):
        return None

    def iter_bytes(self, _n):
        yield b"audio-bytes"


# --------------------------------------------------------------------------
# D34 — 出口闸门
# --------------------------------------------------------------------------
def test_d34_download_to_temp_blocks_loopback():
    with pytest.raises(ValueError):
        download_to_temp("http://127.0.0.1:8080/evil.wav")


def test_d34_download_to_temp_blocks_cloud_metadata():
    with pytest.raises(ValueError):
        download_to_temp("http://169.254.169.254/latest/meta-data/")


def test_d34_download_to_temp_proceeds_when_guard_allows(monkeypatch):
    """守卫放行时，必须真正发起「逐跳校验」的流式请求 —— 证明守卫是出口闸门而非摆设。"""
    calls = {}

    monkeypatch.setattr(ssrf_guard, "is_safe", lambda url: True)

    @contextlib.contextmanager
    def _fake_stream(url, timeout=30, max_hops=5):
        calls["url"] = url
        yield _StreamResp()

    monkeypatch.setattr(ssrf_guard, "guarded_get_stream", _fake_stream)
    path = download_to_temp("http://public.example/ok.wav")
    assert calls.get("url") == "http://public.example/ok.wav"
    assert isinstance(path, str) and path


# --------------------------------------------------------------------------
# H4 — 逐跳校验
# --------------------------------------------------------------------------
_RESOLVED = {
    "cdn.example.com": ["93.184.216.34"],
    "cdn2.example.com": ["93.184.216.35"],
    "169.254.169.254": ["169.254.169.254"],
    "127.0.0.1": ["127.0.0.1"],
}


def _patch_dns(monkeypatch):
    monkeypatch.setattr(SSRFGuard, "_resolved_ips", lambda self, host: _RESOLVED.get(host, []))


class _FakeRedirectResp:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers

    def close(self):
        return None

    def raise_for_status(self):
        return None

    def iter_bytes(self, _n):
        yield b"x"


class _FakeClient:
    """대체 httpx.Client — 스크립트 순서대로 응답을 돌려주며 요청 URL 을 기록."""

    def __init__(self, script, *a, **k):
        self.script = list(script)
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def build_request(self, method, url, headers=None):
        return {"method": method, "url": url, "headers": headers}

    def send(self, req, stream=False, extensions=None):
        self.requested.append(req["url"])
        code, headers = self.script.pop(0)
        return _FakeRedirectResp(code, headers)


def test_h4_redirect_to_cloud_metadata_is_blocked(monkeypatch):
    """公网首跳放行后，302 → 169.254.169.254 必须在下一跳被拦下（旧实现会直连）。"""
    from app.security import ssrf as ssrf_mod

    _patch_dns(monkeypatch)
    script = [(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
    monkeypatch.setattr(ssrf_mod.httpx, "Client", lambda *a, **k: _FakeClient(script))

    with pytest.raises(ValueError) as ei:
        with ssrf_guard.guarded_get_stream("https://cdn.example.com/a.mp3"):
            pass  # pragma: no cover — 不应到达
    assert "169.254.169.254" in str(ei.value)


def test_h4_public_redirect_is_followed(monkeypatch):
    """正常公网重定向仍然可用（不因加固而误伤）。"""
    from app.security import ssrf as ssrf_mod

    _patch_dns(monkeypatch)
    script = [(302, {"location": "https://cdn2.example.com/b.mp3"}), (200, {})]
    fake = _FakeClient(script)
    monkeypatch.setattr(ssrf_mod.httpx, "Client", lambda *a, **k: fake)

    with ssrf_guard.guarded_get_stream("https://cdn.example.com/a.mp3") as resp:
        assert resp.status_code == 200
    # 두 번째 홉은 IP 로 핀ning 되어 요청된다(DNS 재해석 없음)
    assert fake.requested[1].startswith("https://93.184.216.35:")


def test_h4_redirect_loop_is_bounded(monkeypatch):
    """自跳转环路不得无限循环。"""
    from app.security import ssrf as ssrf_mod

    _patch_dns(monkeypatch)
    script = [(302, {"location": "https://cdn.example.com/loop.mp3"})] * 10
    monkeypatch.setattr(ssrf_mod.httpx, "Client", lambda *a, **k: _FakeClient(script))

    with pytest.raises(ValueError) as ei:
        with ssrf_guard.guarded_get_stream("https://cdn.example.com/loop.mp3", max_hops=3):
            pass  # pragma: no cover
    assert "too many redirects" in str(ei.value)


# --------------------------------------------------------------------------
# H5 — IP 固定（DNS-rebinding / TOCTOU）
# --------------------------------------------------------------------------
def test_h5_pin_replaces_netloc_with_validated_ip(monkeypatch):
    monkeypatch.setattr(SSRFGuard, "_resolved_ips", lambda self, host: ["93.184.216.34"])
    pinned, host = ssrf_guard.pin("https://cdn.example.com:8443/a.mp3?x=1")
    assert host == "cdn.example.com"
    assert pinned == "https://93.184.216.34:8443/a.mp3?x=1"


def test_h5_safe_fetch_pins_ip_and_sets_sni(monkeypatch):
    """传入复用 client 时：URL 换成已验证 IP，Host/SNI 仍指向原主机名。"""

    class _Resp:
        status_code = 200
        content = b"ok"

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kw):
            self.calls.append((url, kw))
            return _Resp()

    _patch_dns(monkeypatch)
    c = _Client()
    asyncio.run(ssrf_guard.safe_fetch("https://cdn.example.com/a.mp3", client=c))
    url, kw = c.calls[0]
    assert url == "https://93.184.216.34:443/a.mp3"
    assert kw["headers"]["Host"] == "cdn.example.com"
    assert kw["extensions"] == {"sni_hostname": "cdn.example.com"}


def test_h5_safe_fetch_does_not_use_unsupported_server_name(monkeypatch):
    """回归锁：httpx 0.27.2 的 AsyncHTTPTransport 没有 server_name 参数。

    旧实现写了 ``httpx.AsyncHTTPTransport(server_name=host)`` —— 一旦被调用就是
    TypeError（该函数从未被真正调用，属于潜伏的死代码）。httpx 0.27 的 IP 固定只能
    走 httpcore 的逐请求 ``sni_hostname`` extension。
    """
    from app.security import ssrf as ssrf_mod

    _patch_dns(monkeypatch)
    seen = {}

    class _Resp:
        status_code = 200
        content = b"ok"

        def raise_for_status(self):
            return None

    class _AsyncClient:
        def __init__(self, **kw):
            seen["init"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            seen["get"] = (url, kw)
            return _Resp()

    monkeypatch.setattr(ssrf_mod.httpx, "AsyncClient", _AsyncClient)
    asyncio.run(ssrf_guard.safe_fetch("https://cdn.example.com/a.mp3"))

    assert "transport" not in seen["init"], "不得再构造 server_name transport（httpx 0.27 不支持）"
    url, kw = seen["get"]
    assert url.startswith("https://93.184.216.34:")
    assert kw["extensions"]["sni_hostname"] == "cdn.example.com"
