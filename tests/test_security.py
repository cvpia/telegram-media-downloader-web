from __future__ import annotations

from pathlib import Path

from app.security import SecretCipher, hash_password, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_secret_cipher_round_trip(tmp_path: Path):
    cipher = SecretCipher.load_or_create(tmp_path / ".secret.key")
    encrypted = cipher.encrypt("api-hash-value")
    assert encrypted.startswith("fernet:")
    assert "api-hash-value" not in encrypted
    assert cipher.decrypt(encrypted) == "api-hash-value"


def test_invalid_password_hash_is_rejected():
    assert not verify_password("anything", "not-a-valid-hash")