"""TOTP (RFC 6238) 구현 — 표준 라이브러리만 사용.

왜 직접 구현하는가:
- `pyotp` 는 requirements.txt 에 **선언되지 않은** 의존성이다(환경에 따라 존재하지 않을 수 있다).
  선언되지 않은 패키지에 런타임을 기대는 것은 "환경에 따라 조용히 깨지는" 코드다.
- TOTP 는 RFC 6238 + RFC 4226(HOTP) 로 명세가 고정돼 있고,
  필요한 연산은 HMAC-SHA1 과 base32 뿐이다 — 표준 라이브러리로 30줄이면 끝난다.

검증 대상(테스트 고정 벡터): RFC 6238 부록 B 의 SHA1 케이스.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

ALGORITHM = "SHA1"  # RFC 6238 기본값. Google Authenticator / Authy 호환이 목적이므로 변경 금지.
DIGITS = 6
PERIOD_SECONDS = 30  # RFC 6238 권장 스텝(30초)


def generate_secret(length: int = 20) -> str:
    """base32 비밀키 생성. 기본 20바이트 = 160비트(RFC 4226 §4 권장 최소 강도)."""
    raw = secrets.token_bytes(length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """base32 디코드 — 대소문자/누락된 패딩(=)을 허용한다(QR 수동 입력 관용)."""
    s = secret.strip().replace(" ", "").upper()
    # base32 는 8의 배수 길이를 요구하므로 패딩 보정
    pad = (-len(s)) % 8
    return base64.b32decode(s + "=" * pad, casefold=True)


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP. counter 는 8바이트 빅엔디언."""
    key = _decode_secret(secret)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F  # 동적 트렁케이션
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp(secret: str, at: float | None = None, digits: int = DIGITS) -> str:
    """시각 at(epoch 초)에 대응하는 TOTP."""
    ts = time.time() if at is None else at
    return hotp(secret, int(ts // PERIOD_SECONDS), digits)


def verify(secret: str, code: str, valid_window: int = 1, at: float | None = None) -> bool:
    """코드 검증. valid_window=1 → 현재 스텝 ±1(=±30초) 허용.

    ±1 스텝은 시계 드리프트를 흡수하기 위한 것이고, "±1분" 같은 과장이 아니다.
    비교는 상수시간(hmac.compare_digest) — 타이밍 사이드채널 차단.
    """
    if not secret or not code:
        return False
    candidate = str(code).strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != DIGITS:
        return False
    ts = time.time() if at is None else at
    counter = int(ts // PERIOD_SECONDS)
    for step in range(-valid_window, valid_window + 1):
        if hmac.compare_digest(hotp(secret, counter + step, DIGITS), candidate):
            return True
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "FLOW MUSIC") -> str:
    """otpauth:// URI — QR 코드 생성용 표준 문자열(RFC 바깥, Google Authenticator 관례)."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm={ALGORITHM}&digits={DIGITS}&period={PERIOD_SECONDS}"
    )
