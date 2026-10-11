"""D37 — 음악 JWT 서명 키 기본값 방어 회귀 테스트.

서명(sign) · 검증(verify) · scope 한정 검증(scoped) **양쪽 모두** 공개 기본값
(jwt_secret="change-me-please" 등) 에서 503(fail-closed) 을 내야 한다.
한쪽만 막으면 공격자가 기본값으로 위조한 토큰이 검증을 통과한다(편측 가드 무의미).
"""

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers.auth import create_token, get_current_user, get_current_user_scoped

_INSECURE = "change-me-please"
_SECURE = "a-strong-test-secret-with-enough-entropy-0123456789"


class _FakeReq:
    """get_current_user 现从 Request 的 header/cookie 取令牌；单测用最小替身。"""

    def __init__(self, token: str | None = None):
        self.headers: dict = {}
        self.cookies: dict = {}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"


def test_create_token_rejects_insecure_secret(monkeypatch):
    """[D37-sign] 서명 경로: 기본값 서명 키로는 토큰을 발급하지 않는다(503)."""
    monkeypatch.setattr(settings, "jwt_secret", _INSECURE)
    with pytest.raises(HTTPException) as exc:
        create_token(user_id=1)
    assert exc.value.status_code == 503


def test_create_token_works_with_secure_secret(monkeypatch):
    """[D37-sign] 충분한 엔트로피 키에서는 정상 발급."""
    monkeypatch.setattr(settings, "jwt_secret", _SECURE)
    tok = create_token(user_id=7)
    assert isinstance(tok, str) and len(tok) > 0


def test_get_current_user_rejects_insecure_secret(monkeypatch):
    """[D37-verify] 검증 경로: 기본값 키로는 위조 토큰 검증을 거부한다(503, db 미접근)."""
    monkeypatch.setattr(settings, "jwt_secret", _INSECURE)
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=_FakeReq(), db=None)
    assert exc.value.status_code == 503


def test_get_current_user_scoped_rejects_insecure_secret(monkeypatch):
    """[D37-scoped] scope 한정 검증 경로도 동일하게 503."""
    monkeypatch.setattr(settings, "jwt_secret", _INSECURE)
    dep = get_current_user_scoped("sync:flow")
    with pytest.raises(HTTPException) as exc:
        dep(token="anything", db=None)
    assert exc.value.status_code == 503


def test_forged_token_with_insecure_secret_rejected_when_secure_set(monkeypatch):
    """[D37-verify] 기본값으로 위조한 토큰이, 운영 키(secure) 하에서 검증 실패(401).

    sign 가드만 있고 verify 가드가 없으면 이 경로가 열린다 — 양쪽 가드가 필요함을 증명.
    """
    from jose import jwt

    monkeypatch.setattr(settings, "jwt_secret", _SECURE)  # 운영 키로 검증 경로 설정
    # 공격자가 기본값(key) 으로 직접 위조 — create_token 은 서명단계에서 503 으로 막으므로
    # jose 로 직접 인코딩한다.
    forged = jwt.encode({"sub": "1"}, _INSECURE, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=_FakeReq(forged), db=None)
    assert exc.value.status_code == 401


def test_get_current_user_rejects_token_without_aud(monkeypatch):
    """[SEC] aud/iss 声明：缺 aud/iss 的令牌（跨服务误用/伪造）必须被拒（401）。"""
    monkeypatch.setattr(settings, "jwt_secret", _SECURE)
    from jose import jwt as _jwt

    bad = _jwt.encode({"sub": "1"}, _SECURE, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=_FakeReq(bad), db=None)
    assert exc.value.status_code == 401
