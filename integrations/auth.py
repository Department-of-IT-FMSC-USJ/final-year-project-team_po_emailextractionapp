"""Token encryption and refresh helpers for Graph OAuth."""

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings


def _fernet() -> Fernet | None:
    key = settings.token_encryption_key
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plain: str) -> str:
    f = _fernet()
    if f is None:
        return plain
    return f.encrypt(plain.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    f = _fernet()
    if f is None:
        return encrypted
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Invalid encrypted token") from None
