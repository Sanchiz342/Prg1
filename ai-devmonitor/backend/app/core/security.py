from datetime import timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .db import utcnow

bearer = HTTPBearer(auto_error=False)


def authenticate(username: str, password: str) -> str | None:
    """Returns role for valid credentials, else None."""
    if username == settings.admin_user and password == settings.admin_password:
        return "admin"
    if username == settings.viewer_user and password == settings.viewer_password:
        return "viewer"
    return None


def create_token(username: str, role: str) -> str:
    exp = utcnow() + timedelta(minutes=settings.jwt_ttl_minutes)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return decode_token(creds.credentials)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


def require_ingest_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.ingest_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")


def ws_user(token: str | None = Query(default=None)) -> dict:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    return decode_token(token)
