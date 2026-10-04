"""Engine / session management. The URL decides the backend: sqlite:///… (default) or postgresql+psycopg://…"""
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from .models import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def init_db(url: str | None = None) -> Engine:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    url = url or settings.database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine = create_engine(url, **kwargs)
    _factory = sessionmaker(_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    return _engine


def new_session() -> Session:
    if _factory is None:
        raise RuntimeError("database not initialised; call init_db() first")
    return _factory()
