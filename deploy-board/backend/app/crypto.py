"""Secrets are stored encrypted (Fernet) and only decrypted when a runner needs them."""
from cryptography.fernet import Fernet

from .config import settings

_fernet: Fernet | None = None


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.secret_key.encode() if settings.secret_key else None
        if key is None:
            if settings.key_file.exists():
                key = settings.key_file.read_bytes()
            else:
                key = Fernet.generate_key()
                settings.key_file.write_bytes(key)
                settings.key_file.chmod(0o600)
        _fernet = Fernet(key)
    return _fernet


def reset() -> None:
    global _fernet
    _fernet = None


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _get().decrypt(token.encode()).decode()
