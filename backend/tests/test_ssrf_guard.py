"""음악 SSRF 가드 단위 테스트 (app/security/ssrf.py).

IP 리터럴은 getaddrinfo 가 네트워크 없이 로컬 해석하므로 결정적이다.
외부 도메인(예: example.com) 검증은 DNS 의존이므로 여기선 다루지 않는다.
"""

import pytest

from app.security.ssrf import SSRFGuard, ssrf_guard


def test_blocks_internal_metadata_loopback_private():
    g = SSRFGuard()
    assert g.is_safe("http://169.254.169.254/latest/meta") is False
    assert g.is_safe("http://127.0.0.1/") is False
    assert g.is_safe("http://10.0.0.5/") is False
    assert g.is_safe("http://192.168.1.1/") is False


def test_blocks_disallowed_scheme_and_garbage():
    g = SSRFGuard()
    assert g.is_safe("ftp://example.com/") is False
    assert g.is_safe("file:///etc/passwd") is False
    assert g.is_safe("not-a-url") is False


def test_guard_raises_on_blocked():
    g = SSRFGuard()
    with pytest.raises(ValueError):
        g.guard("http://169.254.169.254/latest/meta")


def test_singleton_exported():
    assert isinstance(ssrf_guard, SSRFGuard)
