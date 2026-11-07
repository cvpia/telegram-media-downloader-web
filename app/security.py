"""Password hashing, encrypted secrets and CSRF helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


class SecretCipher:
    """Encrypt settings at rest with a local Fernet key."""

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def load_or_create(cls, path: Path) -> "SecretCipher":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.write_bytes(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        try:
            return cls(key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Invalid encryption key in {path}") from exc

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"fernet:{token}"

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith("fernet:"):
            return value
        try:
            return self._fernet.decrypt(value[7:].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt stored secret") from exc


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def enforce_csrf(request: Request, supplied_token: str | None) -> None:
    if not request.app.state.runtime.csrf_enabled:
        return
    expected = request.session.get("csrf_token")
    if not expected or not supplied_token or not hmac.compare_digest(expected, supplied_token):
        raise HTTPException(status_code=403, detail="CSRF token 无效，请刷新页面后重试")