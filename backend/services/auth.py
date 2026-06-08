import bcrypt
from itsdangerous import URLSafeTimedSerializer
import os

SECRET_KEY = os.getenv("SECRET_KEY", "changeme-gebruik-een-sterk-wachtwoord")
serializer = URLSafeTimedSerializer(SECRET_KEY)


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})


def verify_session_token(token: str, max_age: int = 86400 * 7):
    try:
        data = serializer.loads(token, max_age=max_age)
        return data.get("user_id")
    except Exception:
        return None
