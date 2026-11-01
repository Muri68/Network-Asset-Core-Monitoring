"""
Encryption for secrets stored in the database (PLAN.md §7.3): the Telegram bot token now, vault
credentials in Phase 7.

Keys come from NETCORE_ENCRYPTION_KEYS (comma-separated Fernet keys, newest first). MultiFernet encrypts
with the first key and still decrypts with the older ones, so a new key can be added in front and
old values re-encrypted later.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _keys():
    keys = list(settings.ENCRYPTION_KEYS)
    if not keys:
        if not settings.DEBUG:
            raise ImproperlyConfigured("Set NETCORE_ENCRYPTION_KEYS (see .env.example) before storing secrets.")
        # Development only: a stable key derived from SECRET_KEY.
        keys = [base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()]
    return MultiFernet([Fernet(k) for k in keys])


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


def new_key():
    return Fernet.generate_key().decode()
