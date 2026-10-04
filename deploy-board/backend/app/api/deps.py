from typing import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .. import db
from ..db import models as m
from ..errors import Unauthorized
from ..jobs import JobQueue
from ..services import auth


def get_session() -> Iterator[Session]:
    with db.new_session() as s:
        yield s


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


def bearer_token(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("authentication required")
    return authorization[7:].strip()


def current_user(token: str = Depends(bearer_token), s: Session = Depends(get_session)) -> m.User:
    return auth.authenticate(s, token)
