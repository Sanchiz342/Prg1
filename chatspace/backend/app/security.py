from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: str, secret: str, ttl_minutes: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    return jwt.encode({"sub": user_id, "exp": exp}, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> str | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None
