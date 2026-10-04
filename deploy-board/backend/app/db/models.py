"""SQLAlchemy models. Portable across SQLite and PostgreSQL (timezone-aware UTC datetimes, JSON column)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ENVIRONMENTS = ("development", "staging", "production")
ACTIVE = ("QUEUED", "RUNNING")
FINISHED = ("SUCCESS", "FAILED")


def now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Always returns timezone-aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10), default="user")  # admin | user
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class AuthToken(Base):
    """Opaque bearer token; only its SHA-256 is stored."""

    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    repo_url: Mapped[str] = mapped_column(String(500))
    pipeline: Mapped[list] = mapped_column(JSON, default=list)
    webhook_secret: Mapped[str] = mapped_column(Text, default="")  # Fernet token
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    environments: Mapped[list["Environment"]] = relationship(
        cascade="all, delete-orphan", order_by="Environment.id", back_populates="project")
    deployments: Mapped[list["Deployment"]] = relationship(
        cascade="all, delete-orphan", order_by="Deployment.id.desc()", back_populates="project")


class Environment(Base):
    """A deploy target (development | staging | production). Owns its variables, secrets and deployments."""

    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(20))
    branch: Mapped[str] = mapped_column(String(100), default="main")
    health_url: Mapped[str] = mapped_column(String(500), default="")
    port: Mapped[int] = mapped_column(Integer, default=0)  # published container port, 0 = none
    auto_deploy: Mapped[bool] = mapped_column(default=True)  # deploy on matching webhook push
    project: Mapped[Project] = relationship(back_populates="environments")
    variables: Mapped[list["Variable"]] = relationship(cascade="all, delete-orphan", order_by="Variable.key")


class Variable(Base):
    __tablename__ = "variables"
    __table_args__ = (UniqueConstraint("environment_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)  # plaintext, or Fernet token when is_secret
    is_secret: Mapped[bool] = mapped_column(default=False)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    commit: Mapped[str] = mapped_column(String(64), default="")
    branch: Mapped[str] = mapped_column(String(100), default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | webhook | rollback
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)  # QUEUED RUNNING SUCCESS FAILED
    image: Mapped[str] = mapped_column(String(200), default="")
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # times a worker picked this job up
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # worker heartbeat deadline
    project: Mapped[Project] = relationship(back_populates="deployments")
    environment: Mapped[Environment] = relationship()
    stages: Mapped[list["Stage"]] = relationship(cascade="all, delete-orphan", order_by="Stage.position")

    @property
    def environment_name(self) -> str:
        return self.environment.name


class Stage(Base):
    __tablename__ = "stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING RUNNING SUCCESS FAILED SKIPPED
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LogLine(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(50), default="")
    line: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
