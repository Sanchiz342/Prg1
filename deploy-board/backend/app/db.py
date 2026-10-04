from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    repo_url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(100), default="main")
    environment: Mapped[str] = mapped_column(String(50), default="production")
    health_url: Mapped[str] = mapped_column(String(500), default="")
    port: Mapped[int] = mapped_column(Integer, default=0)  # container port, 0 = no deploy
    pipeline: Mapped[list] = mapped_column(JSON, default=list)
    webhook_secret: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    variables: Mapped[list["Variable"]] = relationship(cascade="all, delete-orphan")
    deployments: Mapped[list["Deployment"]] = relationship(cascade="all, delete-orphan", order_by="Deployment.id.desc()")


class Variable(Base):
    __tablename__ = "variables"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)  # plaintext, or Fernet token when is_secret
    is_secret: Mapped[bool] = mapped_column(default=False)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    commit: Mapped[str] = mapped_column(String(64), default="")
    branch: Mapped[str] = mapped_column(String(100), default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | webhook | rollback
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED RUNNING SUCCESS FAILED
    image: Mapped[str] = mapped_column(String(200), default="")
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stages: Mapped[list["Stage"]] = relationship(cascade="all, delete-orphan", order_by="Stage.position")


class Stage(Base):
    __tablename__ = "stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING RUNNING SUCCESS FAILED SKIPPED
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LogLine(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), index=True)
    stage: Mapped[str] = mapped_column(String(50), default="")
    line: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


engine = None
SessionLocal = None


def init_db(url: str | None = None) -> None:
    global engine, SessionLocal
    url = url or settings.database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
