import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY = os.getenv("SECRET_KEY", "changeme-gebruik-een-sterk-wachtwoord")


def _fernet() -> Fernet:
    digest = hashlib.sha256(SECRET_KEY.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * 8 + value[-4:]
