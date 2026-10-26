"""대칭 암호화 유틸 — EngineConfig.api_key 등 민감 필드의 저장 암호화(at-rest).

알고리즘과 키 파생 (2026-10-25 교체, 감사 결론):
- 암호화: Fernet (AES-128-CBC + HMAC-SHA256) — `cryptography` (Apache-2.0/BSD).
- 口令 → 키 파생: **PBKDF2-HMAC-SHA256, 600,000 회** — 역시 `cryptography`
  (`cryptography.hazmat.primitives.kdf.pbkdf2.PBKDF2HMAC`, OpenSSL 구현).
  ⚠ 역사적 결함 (수정됨): 이 모듈은 예전에 **단일 SHA-256** 으로 口令에서 키를
  파생했다(무염, 무반복 = 브루트포스 방어 제로). 현재 버전(v2)은 항상 PBKDF2.
  염(salt)은 settings.encryption_salt — 비밀일 필요는 없지만 **배포별 고유**해야
  한다(미설정 시 내장 기본 염, 개발 전용).
- 호환: encryption_key 자체가 유효한 Fernet 키(base64 32B)면 파생 없이 사용.
- 복호화 3단 폴백 (무중단 마이그레이션 보장): v2(PBKDF2) → v1(구 SHA-256) →
  그대로 반환(마이그레이션 전 평문 행).
- 운영 환경에서는 ENCRYPTION_KEY(및 ENCRYPTION_SALT)를 반드시 설정할 것.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings

logger = logging.getLogger(__name__)

# 개발 폴백 키 소재(32바이트, 고정 패스프레이즈에서 파생). 운영 금지.
_DEV_KEY_MATERIAL = "dev-only-insecure-encryption-key-change-me"

# PBKDF2-HMAC-SHA256 반복수 — OWASP Password Storage Cheat Sheet 권고값(600,000).
# 이 키는 사용자 口令이 아니라 운영자가 설정한 고엔트로피 시크릿이며, Fernet
# 인스턴스는 프로세스당 1회 캐시되므로 600k 회(≈0.3s)는 기동 시 1회만 발생한다.
_KDF_ITERATIONS = 600_000

# ENCRYPTION_SALT 미설정 시의 기본 염. 염은 비밀이 아니므로(레인보우 테이블
# 방어용) 하드코딩이 허용되지만, 기본값을 쓰면 모든 배포가 같은 염을 쓰게 되어
# PBKDF2 의 다중 표적 방어가 무력화된다 → 운영은 반드시 자체 염을 설정할 것.
_KDF_SALT_FALLBACK = b"flow-music/at-rest/v1/default-salt"


def _key_material() -> str:
    k = (settings.encryption_key or "").strip()
    return k or _DEV_KEY_MATERIAL


def _salt() -> bytes:
    return (settings.encryption_salt or "").strip().encode("utf-8") or _KDF_SALT_FALLBACK


@lru_cache(maxsize=8)
def _derive_key_cached(material: str, salt: bytes) -> bytes:
    """(material, salt) → Fernet 키 (PBKDF2-HMAC-SHA256, 600k 회).

    ⚠ 캐시는 선택이 아니라 **필수**다: 600k 회 ≈0.27s 이므로 캐시가 없으면
    `decrypt_secret`/`encrypt_secret` 호출마다(즉 모델 속성 접근마다) 0.27s 를
    지불한다. settings 는 프로세스 수명 동안 고정 → 적중률 사실상 100%.
    material/salt 가 바뀌면(테스트/재설정) 키도 새로 파생된다(최대 8개 유지).
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(material.encode("utf-8")))


def _derive_key_pbkdf2(material: str) -> bytes:
    """口令 → Fernet 키 (PBKDF2-HMAC-SHA256, 600k 회). v2 표준 경로."""
    return _derive_key_cached(material, _salt())


def _raw_key() -> bytes:
    """v2: 유효한 Fernet 키면 그대로, 아니면 PBKDF2 파생."""
    material = _key_material()
    try:
        Fernet(material.encode("utf-8"))
        return material.encode("utf-8")
    except Exception:
        return _derive_key_pbkdf2(material)


def _legacy_raw_key() -> bytes:
    """v1 (복호화 호환 전용): 구버전 단일 SHA-256 파생. 신규 암호화 금지."""
    material = _key_material()
    try:
        Fernet(material.encode("utf-8"))
        return material.encode("utf-8")
    except Exception:
        return base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())


_fernet_cache: dict[str, Fernet] = {}


def _fernet(raw: bytes) -> Fernet:
    key_id = raw.decode("utf-8", "ignore")
    f = _fernet_cache.get(key_id)
    if f is None:
        f = Fernet(raw)
        _fernet_cache[key_id] = f
    return f


def encrypt_secret(plain: str | None) -> str | None:
    """평문 → Fernet 토큰 (v2/PBKDF2 키). None/빈 문자열은 None."""
    if not plain:
        return None
    return _fernet(_raw_key()).encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher: str | None) -> str | None:
    """Fernet 토큰 → 평문. 3단 폴백: v2 → v1(구 SHA-256) → 원문 반환.

    빈/None 은 None. 마이그레이션 전 평문 저장 행도 그대로 돌려준다.
    """
    if not cipher:
        return None
    for derive in (_raw_key, _legacy_raw_key):
        try:
            return _fernet(derive()).decrypt(cipher.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            continue
    # 유효한 Fernet 토큰이 아님(구 plaintext 저장 행) — 마이그레이션 전 호환
    return cipher
