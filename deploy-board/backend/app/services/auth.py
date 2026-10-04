import re
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import security
from ..config import settings
from ..db import models as m
from ..db import repositories as repo
from ..errors import Conflict, Forbidden, Invalid, Unauthorized

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_DUMMY_HASH = security.hash_password("deployboard-dummy-password")  # equalises timing for unknown users


def _validate(username: str, password: str) -> None:
    if not _USERNAME.match(username):
        raise Invalid("username must be 3-64 characters: letters, digits, _ . -")
    if len(password) < 8:
        raise Invalid("password must be at least 8 characters")


def _create(s: Session, username: str, password: str, role: str) -> m.User:
    _validate(username, password)
    if repo.get_user_by_name(s, username):
        raise Conflict("username already taken")
    user = m.User(username=username, password_hash=security.hash_password(password), role=role)
    s.add(user)
    s.commit()
    return user


def register_first_user(s: Session, username: str, password: str) -> m.User:
    """Bootstrap: the very first account becomes the admin; afterwards only admins create users."""
    if repo.count_users(s):
        raise Forbidden("registration is closed; ask an admin to create your account")
    return _create(s, username, password, "admin")


def create_user(s: Session, actor: m.User, username: str, password: str, role: str) -> m.User:
    if actor.role != "admin":
        raise Forbidden("admin only")
    if role not in ("admin", "user"):
        raise Invalid("role must be 'admin' or 'user'")
    return _create(s, username, password, role)


def login(s: Session, username: str, password: str) -> tuple[str, m.User]:
    user = repo.get_user_by_name(s, username)
    ok = security.verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise Unauthorized("invalid username or password")
    token = security.new_token()
    now = m.now()
    repo.delete_expired_tokens(s, now)
    s.add(m.AuthToken(user_id=user.id, token_hash=security.token_hash(token),
                      expires_at=now + timedelta(hours=settings.token_ttl_hours)))
    s.commit()
    return token, user


def authenticate(s: Session, token: str | None) -> m.User:
    if not token:
        raise Unauthorized("authentication required")
    row = repo.get_token(s, security.token_hash(token))
    if not row or row.expires_at < m.now():
        raise Unauthorized("invalid or expired token")
    user = repo.get_user(s, row.user_id)
    if not user:
        raise Unauthorized("invalid or expired token")
    return user


def logout(s: Session, token: str) -> None:
    row = repo.get_token(s, security.token_hash(token))
    if row:
        s.delete(row)
        s.commit()
