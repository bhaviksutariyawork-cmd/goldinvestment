"""Access tokens are encrypted at rest.

A Meta access token is a bearer credential for someone else's ad account. It
is never logged, never returned by the API, and never stored in plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class TokenVaultError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenVaultError(
            "CCC_TOKEN_ENCRYPTION_KEY is not set. Generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` before storing a token.'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenVaultError(
            "Stored token could not be decrypted — the encryption key has changed. "
            "Re-enter the access token on the Sync Settings screen."
        ) from exc


def mask(token: str) -> str:
    """What the UI is allowed to see."""
    if not token:
        return ""
    return f"{'*' * 8}{token[-4:]}" if len(token) > 4 else "****"
