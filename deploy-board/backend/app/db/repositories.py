"""Data access only: queries and simple persistence helpers. No business rules live here."""
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session, aliased

from . import models as m


# ---- users / tokens -------------------------------------------------------------
def count_users(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(m.User)) or 0


def get_user(s: Session, user_id: int) -> m.User | None:
    return s.get(m.User, user_id)


def get_user_by_name(s: Session, username: str) -> m.User | None:
    return s.scalar(select(m.User).where(m.User.username == username))


def list_users(s: Session) -> list[m.User]:
    return list(s.scalars(select(m.User).order_by(m.User.id)))


def get_token(s: Session, token_hash: str) -> m.AuthToken | None:
    return s.scalar(select(m.AuthToken).where(m.AuthToken.token_hash == token_hash))


def delete_expired_tokens(s: Session, at: datetime) -> None:
    s.query(m.AuthToken).filter(m.AuthToken.expires_at < at).delete()


# ---- projects / environments ----------------------------------------------------
def list_projects(s: Session, owner_id: int | None = None) -> list[m.Project]:
    q = select(m.Project).order_by(m.Project.name)
    if owner_id is not None:
        q = q.where(m.Project.owner_id == owner_id)
    return list(s.scalars(q))


def get_project(s: Session, project_id: int) -> m.Project | None:
    return s.get(m.Project, project_id)


def get_project_by_name(s: Session, name: str) -> m.Project | None:
    return s.scalar(select(m.Project).where(m.Project.name == name))


def get_environment(s: Session, project_id: int, name: str) -> m.Environment | None:
    return s.scalar(select(m.Environment).where(m.Environment.project_id == project_id, m.Environment.name == name))


# ---- deployments ----------------------------------------------------------------
def get_deployment(s: Session, deployment_id: int) -> m.Deployment | None:
    return s.get(m.Deployment, deployment_id)


def list_deployments(s: Session, project_id: int, environment_id: int | None, limit: int) -> list[m.Deployment]:
    q = select(m.Deployment).where(m.Deployment.project_id == project_id)
    if environment_id is not None:
        q = q.where(m.Deployment.environment_id == environment_id)
    return list(s.scalars(q.order_by(m.Deployment.id.desc()).limit(limit)))


def latest_successful_before(s: Session, environment_id: int, before_id: int) -> m.Deployment | None:
    return s.scalar(select(m.Deployment).where(
        m.Deployment.environment_id == environment_id, m.Deployment.id < before_id,
        m.Deployment.status == "SUCCESS", m.Deployment.image != "").order_by(m.Deployment.id.desc()))


def logs_after(s: Session, deployment_id: int, after: int) -> list[m.LogLine]:
    return list(s.scalars(select(m.LogLine).where(
        m.LogLine.deployment_id == deployment_id, m.LogLine.id > after).order_by(m.LogLine.id)))


def claim_deployment(s: Session, deployment_id: int, lease_until: datetime, at: datetime) -> bool:
    """Atomically move QUEUED -> RUNNING (compare-and-set), but only while no other deployment of the same
    environment is RUNNING. False means: already claimed by someone else, or the environment is busy."""
    d = m.Deployment
    other = aliased(m.Deployment)
    busy = exists().where(other.environment_id == d.environment_id, other.status == "RUNNING", other.id != d.id)
    res = s.execute(update(d).where(d.id == deployment_id, d.status == "QUEUED", ~busy).values(
        status="RUNNING", lease_expires_at=lease_until, started_at=at, attempts=d.attempts + 1))
    s.commit()
    return res.rowcount == 1


def renew_lease(s: Session, deployment_id: int, lease_until: datetime) -> None:
    s.execute(update(m.Deployment).where(m.Deployment.id == deployment_id, m.Deployment.status == "RUNNING")
              .values(lease_expires_at=lease_until))
    s.commit()


def queued_ids(s: Session) -> list[int]:
    return list(s.scalars(select(m.Deployment.id).where(m.Deployment.status == "QUEUED").order_by(m.Deployment.id)))


def expired_running(s: Session, at: datetime) -> list[m.Deployment]:
    return list(s.scalars(select(m.Deployment).where(
        m.Deployment.status == "RUNNING", m.Deployment.lease_expires_at < at)))


def reset_stages(s: Session, deployment: m.Deployment) -> None:
    for st in deployment.stages:
        st.status, st.exit_code, st.started_at, st.finished_at = "PENDING", None, None, None


def lease_for(seconds: float, at: datetime) -> datetime:
    return at + timedelta(seconds=seconds)
