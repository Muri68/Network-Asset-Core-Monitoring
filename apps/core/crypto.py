"""
Encryption for secrets stored in the database (PLAN.md §7.3): vault passwords and notes, the Telegram
bot token.

Keys come from NETCORE_ENCRYPTION_KEYS (comma-separated Fernet keys, newest first). Values are always
encrypted with the first key; older keys still decrypt, so rotating is: put a new key in front, run
`manage.py vault_rotate_key` (re-encrypts everything with it), then drop the old key.

Each key has a short id (`key_id`), stored next to vault ciphertexts so you can see what still uses an
old key.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _key_strings():
    keys = list(settings.ENCRYPTION_KEYS)
    if not keys:
        if not settings.DEBUG:
            raise ImproperlyConfigured("Set NETCORE_ENCRYPTION_KEYS (see .env.example) before storing secrets.")
        # Development only: a stable key derived from SECRET_KEY.
        keys = [base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()]
    return keys


def _keys():
    return MultiFernet([Fernet(k) for k in _key_strings()])


def key_id(key):
    """Short, non-secret fingerprint of a key, e.g. 'k3f9a01c2'."""
    return "k" + hashlib.sha256(key.encode()).hexdigest()[:8]


def current_key_id():
    return key_id(_key_strings()[0])


def encrypt(text):
    return _keys().encrypt(text.encode()).decode()


def decrypt(token):
    """The plain text, or "" if the value can't be decrypted with any configured key."""
    if not token:
        return ""
    try:
        return _keys().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def rotate(token):
    """Re-encrypt `token` with the current key. Raises InvalidToken if no configured key can read it."""
    return _keys().rotate(token.encode()).decode()


def new_key():
    return Fernet.generate_key().decode()


__all__ = ["InvalidToken", "current_key_id", "decrypt", "encrypt", "key_id", "new_key", "rotate"]
