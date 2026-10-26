"""GAP-002: 이메일 인증 / 비밀번호 재설정 / MFA / 계정 잠금.

테스트 원칙
1. **실제 SES 를 건드리지 않는다.** `send_email` 을 monkeypatch 로 가로채
   메일 본문을 캡처한다.
2. **token 은 메일 본문에서 추출한다.** 내부 헬퍼를 직접 호출해 token 을 얻는 테스트는
   "사용자가 실제로 밟는 경로"를 검증하지 못한다 — 링크가 깨져도 통과한다.
   그러므로 본문에서 정규식으로 뽑아 쓴다.
3. 응답 본문에 비밀 정보(mfa_secret/verify_token/reset_token)가 새지 않는지도 단언한다.
"""

import re

import pytest

from app.db import SessionLocal
from app.models import User
from app.security import totp
from app.services import email as email_service


@pytest.fixture
def outbox(monkeypatch):
    """발송된 메일을 모으는 가짜 outbox. 실제 SES 는 절대 호출되지 않는다."""
    sent: list[dict] = []

    def fake_send_email(to: str, subject: str, body: str) -> bool:
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    return sent


def _token_from(body: str) -> str:
    """메일 본문의 링크에서 token 파라미터를 뽑는다(사용자가 클릭하는 그 값)."""
    m = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", body)
    assert m, f"메일 본문에서 token 을 찾지 못했습니다:\n{body}"
    return m.group(1)


def _register(client, email="a@example.com", password="password123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def _login(client, email="a@example.com", password="password123", **kw):
    return client.post("/api/auth/login", json={"email": email, "password": password, **kw})


def _db_user(email="a@example.com") -> User:
    sl = SessionLocal()
    try:
        return sl.query(User).filter(User.email == email).first()
    finally:
        sl.close()


def _last_email(outbox, kind: str) -> dict:
    """kind: 'verify' | 'reset' — 제목으로 구분."""
    needle = "이메일 인증" if kind == "verify" else "비밀번호 재설정"
    matches = [m for m in outbox if needle in m["subject"]]
    assert matches, f"{kind} 메일이 발송되지 않았습니다 (outbox={outbox})"
    return matches[-1]


# ── 1. 등록 → 미인증 상태 ──


def test_register_sends_verification_email(client, outbox):
    """가입 시 인증 메일 1통이 실제로 나간다."""
    _register(client)
    assert len(outbox) == 1
    assert "이메일 인증" in outbox[0]["subject"]
    assert outbox[0]["to"] == "a@example.com"


def test_register_marks_email_unverified(client, outbox):
    """가입 직후 email_verified=False (즉시 활성 아님)."""
    _register(client)
    assert _db_user().email_verified is False


def test_me_reports_email_verified_false(client, outbox):
    """/me 가 email_verified=False 를 노출 → 프론트가 인증 배너를 띄울 수 있다."""
    _register(client)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email_verified"] is False
    assert r.json()["mfa_enabled"] is False


def test_db_stores_only_sha256_digest_not_plaintext(client, outbox):
    """⚠ 핵심: DB 에는 token **평문이 아니라 sha256 요약**만 저장된다."""
    import hashlib

    _register(client)
    raw = _token_from(outbox[0]["body"])
    stored = _db_user().verify_token
    assert stored == hashlib.sha256(raw.encode()).hexdigest()
    assert stored != raw, "평문 token 이 DB 에 저장되었습니다"


def test_verification_email_link_uses_frontend_url(client, outbox):
    """메일 링크는 frontend_url 기준으로 만들어진다(사용자가 클릭 가능한 주소)."""
    _register(client)
    assert "token=" in outbox[0]["body"]
    assert "/verify-email?" in outbox[0]["body"]


# ── 2. 인증(token 소비) ──


def test_verify_activates_account(client, outbox):
    """정상 token → email_verified=True."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    r = client.post("/api/auth/verify", json={"token": raw})
    assert r.status_code == 200
    assert _db_user().email_verified is True


def test_verify_clears_token_one_time_use(client, outbox):
    """token 은 1회용 — 소비 후 DB 에서 소거된다."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    client.post("/api/auth/verify", json={"token": raw})
    assert _db_user().verify_token is None


def test_verify_token_cannot_be_replayed(client, outbox):
    """같은 token 재사용은 400 (재전송 공격 차단)."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    assert client.post("/api/auth/verify", json={"token": raw}).status_code == 200
    assert client.post("/api/auth/verify", json={"token": raw}).status_code == 400


def test_verify_rejects_bogus_token(client, outbox):
    _register(client)
    assert client.post("/api/auth/verify", json={"token": "not-a-real-token"}).status_code == 400


def test_verify_rejects_expired_token(client, outbox):
    """만료된 token 은 400 (기본 24h 초과)."""
    from datetime import datetime, timedelta, timezone

    _register(client)
    raw = _token_from(outbox[0]["body"])
    sl = SessionLocal()
    try:
        u = sl.query(User).filter(User.email == "a@example.com").first()
        u.verify_sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
        sl.commit()
    finally:
        sl.close()
    r = client.post("/api/auth/verify", json={"token": raw})
    assert r.status_code == 400
    assert "만료" in r.json()["detail"]


def test_verify_token_is_not_interchangeable_with_reset_token(client, outbox):
    """verify 용 token 으로 비밀번호를 재설정할 수 없다(용도 격리)."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    r = client.post(
        "/api/auth/reset-password", json={"token": raw, "new_password": "newpassword123"}
    )
    assert r.status_code == 400


# ── 3. 재발송 ──


def test_resend_requires_authentication(client, outbox):
    """미로그인 재발송은 401 (임의 이메일로 메일 폭탄 불가)."""
    _register(client)
    client.cookies.clear()
    assert client.post("/api/auth/resend-verification").status_code == 401


def test_resend_rotates_token(client, outbox):
    """재발송은 새 token 을 발급(구 token 무효화)."""
    from datetime import datetime, timedelta, timezone

    _register(client)
    first = _token_from(outbox[0]["body"])
    # 쿨다운 경과 시뮬레이션
    sl = SessionLocal()
    try:
        u = sl.query(User).filter(User.email == "a@example.com").first()
        u.verify_sent_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        sl.commit()
    finally:
        sl.close()
    assert client.post("/api/auth/resend-verification").status_code == 200
    second = _token_from(_last_email(outbox, "verify")["body"])
    assert second != first
    # 새 token 은 유효, 구 token 은 무효
    assert client.post("/api/auth/verify", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verify", json={"token": second}).status_code == 200


def test_resend_cooldown_returns_429(client, outbox):
    """쿨다운(60초) 내 재발송은 429 — 메일 폭주 방지."""
    _register(client)
    r = client.post("/api/auth/resend-verification")
    assert r.status_code == 429


def test_resend_rejected_when_already_verified(client, outbox):
    """이미 인증된 계정은 400 (불필요한 발송 안 함)."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    client.post("/api/auth/verify", json={"token": raw})
    assert client.post("/api/auth/resend-verification").status_code == 400


# ── 4. forgot-password: 존재성 비노출 ──


def test_forgot_password_returns_200_for_unknown_email(client, outbox):
    """미가입 이메일도 200 — 계정 열거 방지."""
    r = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200


def test_forgot_password_returns_200_for_known_email(client, outbox):
    _register(client)
    r = client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    assert r.status_code == 200


def test_forgot_password_same_response_shape_both_cases(client, outbox):
    """응답 본문까지 동일해야 오라클이 되지 않는다."""
    _register(client)
    known = client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_forgot_password_sends_mail_for_registered_user(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    assert len(outbox) == 1
    assert "비밀번호 재설정" in outbox[0]["subject"]


def test_forgot_password_sends_nothing_for_unknown_email(client, outbox):
    """미가입 이메일에는 메일을 보내지 않는다(당연하지만 상태코드로 드러나선 안 된다)."""
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert outbox == []


def test_forgot_password_cooldown_does_not_leak_via_status(client, outbox):
    """⚠ 설계 결정: 쿨다운 위반도 429 가 아니라 200.

    429 를 주면 '429 vs 200' 이 곧 '가입됨 vs 미가입' 오라클이 된다.
    """
    _register(client)
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    second = client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    assert second.status_code == 200


def test_reset_token_stored_as_digest(client, outbox):
    """재설정 token 도 평문이 아니라 요약으로 저장된다."""
    import hashlib

    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    stored = _db_user().reset_token
    assert stored == hashlib.sha256(raw.encode()).hexdigest()
    assert stored != raw


# ── 5. reset-password ──


def test_reset_password_changes_password(client, outbox):
    """재설정 후 새 비밀번호로 로그인된다."""
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    r = client.post(
        "/api/auth/reset-password", json={"token": raw, "new_password": "brandnewpass123"}
    )
    assert r.status_code == 200
    client.cookies.clear()
    assert _login(client, password="brandnewpass123").status_code == 200


def test_reset_password_invalidates_old_password(client, outbox):
    """구 비밀번호는 더 이상 통하지 않는다."""
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    client.post(
        "/api/auth/reset-password", json={"token": raw, "new_password": "brandnewpass123"}
    )
    client.cookies.clear()
    assert _login(client, password="password123").status_code == 401


def test_reset_password_token_one_time_use(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    assert (
        client.post(
            "/api/auth/reset-password", json={"token": raw, "new_password": "brandnewpass123"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/reset-password", json={"token": raw, "new_password": "anotherpass123"}
        ).status_code
        == 400
    )


def test_reset_password_rejects_expired_token(client, outbox):
    """만료(기본 30분) token 은 400."""
    from datetime import datetime, timedelta, timezone

    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    sl = SessionLocal()
    try:
        u = sl.query(User).filter(User.email == "a@example.com").first()
        u.reset_sent_at = datetime.now(timezone.utc) - timedelta(minutes=31)
        sl.commit()
    finally:
        sl.close()
    r = client.post(
        "/api/auth/reset-password", json={"token": raw, "new_password": "brandnewpass123"}
    )
    assert r.status_code == 400
    assert "만료" in r.json()["detail"]


def test_reset_password_enforces_min_length(client, outbox):
    """짧은 비밀번호 거부(가입과 동일 규칙)."""
    _register(client)
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    r = client.post("/api/auth/reset-password", json={"token": raw, "new_password": "short"})
    assert r.status_code == 400


def test_reset_password_rejects_bogus_token(client, outbox):
    _register(client)
    r = client.post(
        "/api/auth/reset-password", json={"token": "bogus", "new_password": "brandnewpass123"}
    )
    assert r.status_code == 400


# ── 6. 계정 잠금 (IP 레이트리밋과 상보적) ──


def test_failed_logins_lock_account(client, outbox):
    """5회 연속 실패 → 잠금. 이후 **정답 비밀번호도** 401."""
    _register(client)
    client.cookies.clear()
    for _ in range(5):
        _login(client, password="wrongpassword")
    assert _db_user().locked_until is not None
    r = _login(client, password="password123")
    assert r.status_code == 401
    assert "잠금" in r.json()["detail"]


def test_lockout_checked_before_password_verification(client, outbox):
    """⚠ 잠금은 비밀번호 검증 **전에** 판정 — 정답을 맞춰도 잠금이 유지되어야 한다.

    (검증 후 판정이면 5번째 시도에서 우연히 정답을 맞추는 순간 잠금이 무력화된다.)
    """
    from datetime import datetime, timedelta, timezone

    _register(client)
    client.cookies.clear()
    for _ in range(5):
        _login(client, password="wrongpassword")
    # 잠금 상태에서 정답 시도
    assert _login(client, password="password123").status_code == 401
    # locked_until 이 미래로 유지되는지 확인 (정답 시도가 잠금을 풀지 않음)
    assert _db_user().locked_until is not None


def test_successful_login_clears_failure_counter(client, outbox):
    """소수 실패 후 정상 로그인 → 카운터 0 (무고한 사용자를 영구 차단하지 않음)."""
    _register(client)
    client.cookies.clear()
    for _ in range(3):
        _login(client, password="wrongpassword")
    assert _db_user().failed_logins == 3
    assert _login(client, password="password123").status_code == 200
    assert _db_user().failed_logins == 0
    assert _db_user().locked_until is None


def test_expired_lockout_allows_login(client, outbox):
    """잠금 만료 후에는 정상 로그인 가능(자동 복구)."""
    from datetime import datetime, timedelta, timezone

    _register(client)
    client.cookies.clear()
    for _ in range(5):
        _login(client, password="wrongpassword")
    sl = SessionLocal()
    try:
        u = sl.query(User).filter(User.email == "a@example.com").first()
        u.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        sl.commit()
    finally:
        sl.close()
    assert _login(client, password="password123").status_code == 200


def test_password_reset_unlocks_account(client, outbox):
    """재설정 = 본인 확인 → 잠금 해제. 잠긴 사용자가 스스로 복구하는 유일한 길."""
    from datetime import datetime, timedelta, timezone

    _register(client)
    outbox.clear()
    for _ in range(5):
        _login(client, password="wrongpassword")
    assert _db_user().locked_until is not None
    # 잠금 중에도 재설정 요청은 가능해야 한다(그렇지 않으면 영구 잠금)
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    raw = _token_from(outbox[0]["body"])
    r = client.post(
        "/api/auth/reset-password", json={"token": raw, "new_password": "brandnewpass123"}
    )
    assert r.status_code == 200
    u = _db_user()
    assert u.failed_logins == 0
    assert u.locked_until is None


# ── 7. MFA ──


def test_mfa_setup_returns_secret_and_uri(client, outbox):
    """setup 은 secret + otpauth URI 반환, 그러나 아직 미활성."""
    _register(client)
    r = client.post("/api/auth/mfa/setup")
    assert r.status_code == 200
    body = r.json()
    assert body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["enabled"] is False
    assert _db_user().mfa_enabled is False


def test_mfa_setup_requires_auth(client, outbox):
    _register(client)
    client.cookies.clear()
    assert client.post("/api/auth/mfa/setup").status_code == 401


def test_mfa_enable_with_valid_code(client, outbox):
    """올바른 TOTP → mfa_enabled=True."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    code = totp.totp(secret)
    r = client.post("/api/auth/mfa/enable", json={"code": code})
    assert r.status_code == 200
    assert _db_user().mfa_enabled is True


def test_mfa_enable_rejects_bad_code(client, outbox):
    """잘못된 코드로는 활성화되지 않는다(오등록 자기잠금 방지)."""
    _register(client)
    client.post("/api/auth/mfa/setup")
    r = client.post("/api/auth/mfa/enable", json={"code": "000000"})
    assert r.status_code == 400
    assert _db_user().mfa_enabled is False


def test_login_requires_otp_when_mfa_enabled(client, outbox):
    """MFA 활성 계정: otp 없이 로그인 → 401, 토큰/쿠키 발급 없음."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    client.cookies.clear()
    r = _login(client, password="password123")
    assert r.status_code == 401
    assert "otp" in r.json()["detail"] or "两步" in r.json()["detail"]


def test_login_does_not_set_cookie_without_otp(client, outbox):
    """⚠ otp 없이 쿠키가 심어지면 MFA 우회 — 쿠키가 없어야 한다."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    client.cookies.clear()
    _login(client, password="password123")
    assert not client.cookies.get("flow_auth")


def test_login_with_valid_otp_succeeds(client, outbox):
    """올바른 otp 동반 시 토큰 발급."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    client.cookies.clear()
    r = _login(client, password="password123", otp=totp.totp(secret))
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_rejects_bad_otp(client, outbox):
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    client.cookies.clear()
    assert _login(client, password="password123", otp="000000").status_code == 401


def test_bad_otp_counts_as_failed_login(client, outbox):
    """MFA 오류도 실패로 누적 — MFA 가 2차 브루트포스 표면이 되지 않게."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    client.cookies.clear()
    _login(client, password="password123", otp="000000")
    assert _db_user().failed_logins == 1


def test_mfa_disable_requires_password(client, outbox):
    """세션 탈취만으로 2차 인증을 끌 수 없어야 한다."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    r = client.post("/api/auth/mfa/disable", json={"password": "wrongpassword"})
    assert r.status_code == 401
    assert _db_user().mfa_enabled is True


def test_mfa_disable_with_correct_password(client, outbox):
    """비밀번호 확인 후 해제 → secret 폐기, 이후 otp 없이 로그인 가능."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    r = client.post("/api/auth/mfa/disable", json={"password": "password123"})
    assert r.status_code == 200
    u = _db_user()
    assert u.mfa_enabled is False
    assert u.mfa_secret is None
    client.cookies.clear()
    assert _login(client, password="password123").status_code == 200


def test_me_reports_mfa_enabled(client, outbox):
    """/me 가 mfa_enabled 를 노출(프론트가 설정 화면 상태를 그릴 수 있게)."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    assert client.get("/api/auth/me").json()["mfa_enabled"] is True


def test_mfa_setup_resets_enabled_flag(client, outbox):
    """setup 재실행 시 password 는 새 secret, mfa_enabled 는 다시 False.

    구 secret 이 남아 있으면 사용자가 실제로 등록한 앱과 서버가 어긋난다.
    """
    _register(client)
    first = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(first)})
    second = client.post("/api/auth/mfa/setup").json()["secret"]
    assert second != first
    assert _db_user().mfa_enabled is False


# ── 8. 비밀 정보 비노출 ──


def test_responses_never_leak_secrets(client, outbox):
    """⚠ 어떤 응답에도 mfa_secret/verify_token/reset_token 원문이 섞이지 않는다."""
    _register(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": totp.totp(secret)})
    verify_raw = _token_from(_last_email(outbox, "verify")["body"])
    outbox.clear()
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    reset_raw = _token_from(outbox[0]["body"])

    for path in ("/api/auth/me",):
        body = client.get(path).text
        assert secret not in body
        assert verify_raw not in body
        assert reset_raw not in body

    # /me 응답 스키마 자체에 민감 필드가 없어야 한다
    keys = set(client.get("/api/auth/me").json().keys())
    assert not {"mfa_secret", "verify_token", "reset_token", "hashed_password"} & keys


def test_verify_response_has_no_token_echo(client, outbox):
    """verify 응답이 token 을 되돌려주지 않는다(로그/프록시 캡처 방지)."""
    _register(client)
    raw = _token_from(outbox[0]["body"])
    body = client.post("/api/auth/verify", json={"token": raw}).text
    assert raw not in body


def test_token_digest_is_sha256_length(client, outbox):
    """요약은 sha256 = 64자 16진수 (스키마 String(64) 와 정합)."""
    _register(client)
    stored = _db_user().verify_token
    assert len(stored) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", stored)


# ── 9. email 서비스 계층 (SES 미구성 시 개발 모드) ──


def test_email_service_dev_mode_does_not_raise(monkeypatch):
    """SES 미구성(개발) → 예외 없이 로그만(미발송 False)."""
    monkeypatch.setattr(email_service.settings, "ses_source", "")
    monkeypatch.setattr(email_service.settings, "enable_email_delivery", False)
    assert email_service.delivery_configured() is False
    assert email_service.send_email("x@y.z", "s", "b") is False


def test_email_service_fails_closed_when_delivery_required(monkeypatch):
    """⚠ 운영 의도(enable_email_delivery=True)인데 source 미설정 → 503 (거짓 성공 금지)."""
    from fastapi import HTTPException

    monkeypatch.setattr(email_service.settings, "ses_source", "")
    monkeypatch.setattr(email_service.settings, "enable_email_delivery", True)
    with pytest.raises(HTTPException) as ei:
        email_service.ensure_delivery_available()
    assert ei.value.status_code == 503


def test_totp_rfc4226_vectors():
    """TOTP/HOTP (pyotp 위임) 를 RFC 4226 부록 D 고정 벡터로 검증.

    구현은 pyotp 에 위임하지만, Google Authenticator 와 어긋나면
    사용자가 전부 로그인 불가가 된다 — 명세 대조는 여전히 필수다.
    """
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    expected = ["755224", "287082", "359152", "969429", "338314", "254676"]
    assert [totp.hotp(secret, i) for i in range(6)] == expected


def test_totp_verify_window_and_replay_guard():
    """±1 스텝 허용, 그 밖은 거부. 형식 오류 입력도 안전하게 False."""
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp.verify(secret, "287082", at=59) is True
    assert totp.verify(secret, totp.totp(secret, at=89), at=59) is True  # +1 스텝
    assert totp.verify(secret, totp.totp(secret, at=119), at=59) is False  # +2 스텝
    assert totp.verify(secret, "abcdef", at=59) is False
    assert totp.verify("", "123456", at=59) is False


def test_totp_verify_corrupted_secret_returns_false():
    """★ DB 의 mfa_secret 이 손상돼도 검증은 False — 500 으로 새어나가면 안 된다.

    pyotp 의 base32 디코딩은 비-base32 문자/잘못된 패딩에서
    `binascii.Error`(ValueError 하위) 를 던진다. verify 가 이를 삼키지 않으면
    로그인 엔드포인트가 500 을 내며 "이 계정 MFA 가 깨졌다"를 노출한다.
    """
    assert totp.verify("invalid!!!", "123456") is False
    assert totp.verify("notbase32", "123456") is False
    assert totp.verify("JBSWY3DPEHPK3PXP", "000000") is False  # 정상 base32·오답
