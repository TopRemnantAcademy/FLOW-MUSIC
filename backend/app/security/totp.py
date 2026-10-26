"""TOTP/HOTP (RFC 6238 / RFC 4226) — 구현은 전부 **pyotp** 에 위임.

为什么换成库（2026-10-25 替换，审计结论）：
- 原版是本仓库手写（stdlib `hmac` + `base32`）。虽当时有「规避未声明依赖」的
  论证，但 pyotp 是 MIT 许可的成熟实现，且现已钉入 requirements.txt
  （环境确定性可用，不再有「环境里没装」问题）。
- 内置能力全部来自库：常量时间比较（`hmac.compare_digest`，见 pyotp/utils.py:
  strings_equal）、valid_window 漂移容忍、RFC 4226 附录 D 兼容的 HOTP 向量。
- 本文件只保留**本项目的调用面**（`generate_secret/totp/hotp/verify/
  provisioning_uri`，auth.py 直接依赖），运算零手写。

검증 대상(테스트 고정 벡터): RFC 6238 부록 B / RFC 4226 부록 D 의 SHA1 케이스
— tests/test_auth_gap002.py 에 그대로 존속하며 전부 통과한다.
"""

from __future__ import annotations

import pyotp

ALGORITHM = "SHA1"  # RFC 6238 기본값. Google Authenticator / Authy 호환 목적 → 변경 금지.
DIGITS = 6
PERIOD_SECONDS = 30  # RFC 6238 권장 스텝(30초)
DEFAULT_ISSUER = "FLOW MUSIC"


def generate_secret() -> str:
    """base32 密钥生成. pyotp.random_base32() 기본 32 자리 = 160 비트
    (RFC 4226 §4 권장 최소 강도, 구手搓 구현의 20 바이트와 동등)."""
    return pyotp.random_base32()


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP. counter 는 8바이트 빅엔디언 (pyotp 가 직렬화)."""
    return pyotp.HOTP(secret, digits=digits).at(counter)


def totp(secret: str, at: float | None = None, digits: int = DIGITS) -> str:
    """시각 at(epoch 초)에 대응하는 TOTP. at=None 이면 현재 시각."""
    t = pyotp.TOTP(secret, digits=digits, interval=PERIOD_SECONDS)
    return t.now() if at is None else t.at(at)


def verify(secret: str, code: str, valid_window: int = 1, at: float | None = None) -> bool:
    """코드 검증. valid_window=1 → 현재 스텝 ±1(=±30초, 시계 드리프트 흡수).

    비교는 pyotp 내부에서 hmac.compare_digest (상수시간) — 타이밍
    사이드채널 차단. 형식 오류 입력(비숫자/오답)은 자연스럽 False.

    ⚠ DB 의 mfa_secret 이 손상된 경우(비-base32 문자/잘못된 패딩) pyotp 는
    `binascii.Error`(ValueError 하위) 를 던진다 → 검증 함수는 예외를 밖으로
    흘리지 않는다(로그인 500 으로 "이 계정 MFA 가 깨졌다"를 노출하지 않기 위해).
    손상 secret = 검증 실패로 처리한다.
    """
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret, digits=DIGITS, interval=PERIOD_SECONDS).verify(
            str(code).strip(), for_time=at, valid_window=valid_window
        )
    except (ValueError, TypeError):
        return False


def provisioning_uri(secret: str, account: str, issuer: str = DEFAULT_ISSUER) -> str:
    """otpauth:// URI — QR 코드 생성용 표준 문자열(Google Authenticator 관례)."""
    return pyotp.TOTP(secret, digits=DIGITS, interval=PERIOD_SECONDS).provisioning_uri(
        name=account, issuer_name=issuer
    )
