"""Encryption for secrets stored in the database (Schoology tokens, parents' AI keys)."""

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet:
    if not config.TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Stored secret cannot be decrypted; was TOKEN_ENCRYPTION_KEY changed?") from e
