"""대칭 암호화 유틸 — EngineConfig.api_key 등 민감 필드의 저장 암호화(at-rest).

- 알고리즘: Fernet (AES-128-CBC + HMAC-SHA256). 키는 settings.encryption_key 에서 파생.
- 운영 환경에서는 ENCRYPTION_KEY 를 반드시 설정할 것(임의 패스프레이즈 또는 32바이트 base64 Fernet 키).
- 미설정 시 고정 개발 키로 폴백(로컬/CI 전용 — 운영에선 경고 출력 권장).
- decrypt_secret 은 「유효한 Fernet 토큰이 아닌 구 plaintext」에 대해 호환 복호화(灰度 마이그레이션)한다.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# 개발 폴백 키 소재(32바이트, 고정 패스프레이즈에서 파생). 운영 금지.
_DEV_KEY_MATERIAL = "dev-only-insecure-encryption-key-change-me"


def _raw_key() -> bytes:
    k = (settings.encryption_key or "").strip()
    if not k:
        k = _DEV_KEY_MATERIAL
    # 이미 유효한 Fernet 키면 그대로, 아니면 SHA-256 으로 고정 32바이트 파생
    try:
        Fernet(k.encode("utf-8"))
        return k.encode("utf-8")
    except Exception:
        return base64.urlsafe_b64encode(hashlib.sha256(k.encode("utf-8")).digest())


_fernet_cache: dict[str, Fernet] = {}


def _fernet() -> Fernet:
    raw = _raw_key()
    key_id = raw.decode("utf-8", "ignore")
    f = _fernet_cache.get(key_id)
    if f is None:
        f = Fernet(raw)
        _fernet_cache[key_id] = f
    return f


def encrypt_secret(plain: str | None) -> str | None:
    """평문 → Fernet 토큰. None/빈 문자열은 None 반환(빈 키는 암호화하지 않음)."""
    if not plain:
        return None
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher: str | None) -> str | None:
    """Fernet 토큰 → 평문. 빈/None 은 None. 유효하지 않은 토큰(구 plaintext)は 그대로 반환."""
    if not cipher:
        return None
    try:
        return _fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        # 마이그레이션 전 plaintext 저장 행 호환
        return cipher
