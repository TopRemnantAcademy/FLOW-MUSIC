"""EngineConfig.api_key 静态加密(GAP-007) 单测。"""

import base64
import hashlib

from app.models import EngineConfig
from app.security.crypto import decrypt_secret, encrypt_secret


def test_encrypt_decrypt_roundtrip():
    plain = "sk-secret-12345"
    cipher = encrypt_secret(plain)
    assert cipher != plain
    assert decrypt_secret(cipher) == plain


def test_none_and_empty():
    assert encrypt_secret(None) is None
    assert encrypt_secret("") is None
    assert decrypt_secret(None) is None
    assert decrypt_secret("") is None


def test_legacy_plaintext_compat():
    # 迁移前明文存储的行应原样返回，不抛错
    assert decrypt_secret("plain-old-key") == "plain-old-key"


def test_v1_sha256_token_still_decrypts(monkeypatch):
    """★ 零停机迁移断言：v1（单趟 SHA-256 派生）时期写入的密文必须仍能解开。

    这里按**旧算法的原样**在测试内重放一遍派生+加密（不是产品代码路径），
    模拟生产库里 2026-10-25 之前落库的存量 token。
    """
    from cryptography.fernet import Fernet

    from app.security.crypto import _key_material

    legacy_key = base64.urlsafe_b64encode(
        hashlib.sha256(_key_material().encode("utf-8")).digest()
    )
    token = Fernet(legacy_key).encrypt(b"old-secret-value").decode()
    # 产品代码的 decrypt 必须认这条老 token（v2 失败 → v1 回退命中）
    assert decrypt_secret(token) == "old-secret-value"


def test_model_helper():
    e = EngineConfig(provider="x")
    e.set_api_key("topsecret")
    assert e.api_key != "topsecret"  # 存储为密文
    assert e.get_api_key() == "topsecret"
    # 空值不加密为令牌
    e.set_api_key("")
    assert e.api_key == ""
    assert e.get_api_key() is None
