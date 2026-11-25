"""Encrypts account access before it is stored in Notion.

The key is derived from the ENCRYPTION_PASSPHRASE GitHub secret, so anyone who can read
Notion only sees scrambled text.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

_SALT = b"trackk-ugc-stats-v1"
_ITERATIONS = 200_000


class CryptoError(Exception):
    pass


def _fernet(passphrase: str) -> Fernet:
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), _SALT, _ITERATIONS, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(data: dict, passphrase: str) -> str:
    return _fernet(passphrase).encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def decrypt(blob: str, passphrase: str) -> dict:
    try:
        raw = _fernet(passphrase).decrypt(blob.strip().encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise CryptoError(
            "Could not unlock stored access. Was ENCRYPTION_PASSPHRASE changed? Reconnect the account."
        ) from exc
    return json.loads(raw.decode("utf-8"))
