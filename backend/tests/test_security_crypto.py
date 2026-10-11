"""EngineConfig.api_key 静态加密(GAP-007) 单测。"""

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


def test_model_helper():
    e = EngineConfig(provider="x")
    e.set_api_key("topsecret")
    assert e.api_key != "topsecret"  # 存储为密文
    assert e.get_api_key() == "topsecret"
    # 空值不加密为令牌
    e.set_api_key("")
    assert e.api_key == ""
    assert e.get_api_key() is None
