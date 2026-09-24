# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Fernet encryption, keyed by ``SECRET_KEY``, for secrets that have to be read back.

Changing ``SECRET_KEY`` makes the stored secrets unreadable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


__all__ = [
    'DecryptionError',
    'decrypt_json',
    'encrypt_json',
]


class DecryptionError(Exception):
    """The stored value cannot be decrypted with the current key."""


def _fernet() -> Fernet:
    # Not cached, so a changed SECRET_KEY takes effect immediately.
    digest = hashlib.sha256(b'bublik.core.crypto:' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(value: Any) -> str:
    """Serialize ``value`` as compact JSON and encrypt it."""
    payload = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_json(token: str) -> Any:
    """Decrypt a token produced by :func:`encrypt_json`."""
    try:
        payload = _fernet().decrypt(token.encode())
    except InvalidToken as e:
        msg = 'stored value cannot be decrypted with the current SECRET_KEY'
        raise DecryptionError(msg) from e
    return json.loads(payload)
