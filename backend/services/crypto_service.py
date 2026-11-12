"""Centralized encryption and decryption service for sensitive credentials.

Uses AES encryption (via Fernet) with a 32-byte key derived from
ENCRYPTION_KEY, DB_ENCRYPTION_KEY, or SESSION_SECRET / SECRET_KEY in .env.
Values stored in the database are prefixed with 'enc:v1:' so they can be
identified and decrypted transparently, while keeping legacy plaintext data
readable for backward compatibility.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any, Set

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

_CIPHER_PREFIX = "enc:v1:"
_fernet_instance = None

# Keywords in key names that denote sensitive data to be encrypted
_SENSITIVE_KEYWORDS: Set[str] = {
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "SDK_TOKEN",
    "CONNECT_SID",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "_MK",
}

# Exact key names (lowercase) known to store sensitive tokens or credentials
_SENSITIVE_EXACT_KEYS: Set[str] = {
    "bkav_partner_guid",
    "bkav_partner_token",
    "gmail_refresh_token",
    "gmail_access_token",
    "saveinvoice_password",
    "saveinvoice_access_token",
    "saveinvoice_refresh_token",
    "saveinvoice_connect_sid",
    "imou_app_secret",
    "imou_mk",
    "google_ads_developer_token",
    "google_ads_refresh_token",
    "google_client_secret",
    "imou_email",
    "gmail_sync_email",
    "saveinvoice_username",
    "saveinvoice_account_id",
}

# Keys that may match substrings but must NEVER be encrypted
_NON_SENSITIVE_EXACT_KEYS: Set[str] = {
    "api_keys_managed_names",
    "require_kyc",
    "last_discovery_at",
    "test_key",
}


# Table columns stored encrypted. Writers call encrypt_field(); every query read through
# database.SecureDictCursor gets these back as plain text (decrypt_row).
SENSITIVE_COLUMNS = frozenset({
    "hashed_password",
    "id_number",
    "date_of_birth",
    "phone",
    "email",
    "bank_account",
    "director_phone",
    "director_email",
    "u_bank_acc",
})


def _resolve_raw_secret() -> str:
    """Fetch the secret key from environment or .env file."""
    for env_var in ("DB_ENCRYPTION_KEY", "ENCRYPTION_KEY", "SESSION_SECRET", "SECRET_KEY"):
        val = os.getenv(env_var)
        if val and val.strip():
            return val.strip()

    load_dotenv(find_dotenv() or None)
    for env_var in ("DB_ENCRYPTION_KEY", "ENCRYPTION_KEY", "SESSION_SECRET", "SECRET_KEY"):
        val = os.getenv(env_var)
        if val and val.strip():
            return val.strip()

    raise RuntimeError("No DB_ENCRYPTION_KEY, ENCRYPTION_KEY, SESSION_SECRET or SECRET_KEY is set.")


def _get_fernet():
    """Lazily initialize Fernet cipher instance."""
    global _fernet_instance
    if _fernet_instance is None:
        from cryptography.fernet import Fernet
        raw = _resolve_raw_secret().encode("utf-8")
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
        _fernet_instance = Fernet(derived_key)
    return _fernet_instance


def is_sensitive_key(key: str) -> bool:
    """Determine whether a given configuration/settings key represents sensitive data."""
    if not key or not isinstance(key, str):
        return False
    clean = key.strip().lower()
    if clean in _NON_SENSITIVE_EXACT_KEYS:
        return False
    if clean in _SENSITIVE_EXACT_KEYS:
        return True
    upper = key.upper()
    return any(keyword in upper for keyword in _SENSITIVE_KEYWORDS)


def is_encrypted(val: Any) -> bool:
    """Check if value has the encrypted prefix."""
    return isinstance(val, str) and val.startswith(_CIPHER_PREFIX)


def encrypt_value(plain_text: Any) -> str:
    """Encrypt a plaintext string using AES-Fernet with 'enc:v1:' prefix.
    
    If the value is already encrypted, empty, or None, returns it unmodified.
    """
    if plain_text is None:
        return ""
    text = str(plain_text)
    if not text:
        return ""
    if is_encrypted(text):
        return text

    try:
        cipher = _get_fernet()
        token = cipher.encrypt(text.encode("utf-8")).decode("ascii")
        return f"{_CIPHER_PREFIX}{token}"
    except Exception as exc:
        logger.exception("Failed to encrypt value: %s", exc)
        return text


def decrypt_value(cipher_text: Any) -> str:
    """Decrypt an 'enc:v1:' prefixed ciphertext back to plain text.
    
    If the value is unencrypted (legacy plain text), empty, or None,
    returns it unmodified (backward compatibility).
    """
    if cipher_text is None:
        return ""
    text = str(cipher_text)
    if not text or not is_encrypted(text):
        return text

    try:
        cipher = _get_fernet()
        raw_token = text[len(_CIPHER_PREFIX):].encode("ascii")
        return cipher.decrypt(raw_token).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to decrypt value (corrupted token or changed key): %s", exc)
        return text


def encrypt_field(value: Any) -> Any:
    """Encrypt a value headed for a SENSITIVE_COLUMNS column; NULL and '' stay as they are."""
    if value is None or value == "":
        return value
    return encrypt_value(str(value))


def decrypt_row(row):
    """Decrypt the SENSITIVE_COLUMNS values of one result row (a dict) in place."""
    if row:
        for key in SENSITIVE_COLUMNS.intersection(row.keys()):
            if is_encrypted(row[key]):
                row[key] = decrypt_value(row[key])
    return row
