import re
import secrets

from sqlalchemy.orm import Session

from .. import crypto
from ..db import ACTIVE, ENVIRONMENTS
from ..db import models as m
from ..db import repositories as repo
from ..errors import Conflict, Invalid, NotFound
from ..pipeline.definition import DEFAULT_PIPELINE, validate

_VAR_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MASK = "••••••••"


# ---- access control -----------------------------------------------------------------
def can_access(user: m.User, project: m.Project) -> bool:
    return user.role == "admin" or project.owner_id == user.id


def get_project(s: Session, user: m.User, project_id: int) -> m.Project:
    """Projects of other users are reported as missing rather than forbidden (no existence leak)."""
    p = repo.get_project(s, project_id)
    if not p or not can_access(user, p):
        raise NotFound("project not found")
    return p


def list_projects(s: Session, user: m.User) -> list[m.Project]:
    return repo.list_projects(s, None if user.role == "admin" else user.id)


def get_environment(s: Session, project: m.Project, name: str) -> m.Environment:
    env = repo.get_environment(s, project.id, name)
    if not env:
        raise NotFound(f"environment {name!r} not found")
    return env


def get_deployment(s: Session, user: m.User, deployment_id: int) -> m.Deployment:
    d = repo.get_deployment(s, deployment_id)
    if not d or not can_access(user, d.project):
        raise NotFound("deployment not found")
    return d


# ---- validation ------------------------------------------------------------------------
def _check_env_fields(name: str | None, branch: str | None, health_url: str | None, port: int | None) -> None:
    if name is not None and name not in ENVIRONMENTS:
        raise Invalid(f"environment must be one of {', '.join(ENVIRONMENTS)}")
    if branch is not None and not branch.strip():
        raise Invalid("branch must not be empty")
    if health_url and not re.match(r"^https?://", health_url):
        raise Invalid("health_url must start with http:// or https://")
    if port is not None and not 0 <= port <= 65535:
        raise Invalid("port must be between 0 and 65535")


def _pipeline(stages: list[dict] | None) -> list[dict]:
    try:
        return validate(stages) if stages else [dict(s) for s in DEFAULT_PIPELINE]
    except ValueError as e:
        raise Invalid(str(e))


# ---- projects ---------------------------------------------------------------------------
def create_project(s: Session, user: m.User, name: str, repo_url: str, pipeline: list[dict] | None,
                   environments: list[dict]) -> m.Project:
    if repo.get_project_by_name(s, name):
        raise Conflict("project name already exists")
    envs = environments or [{"name": "production", "branch": "main"}]
    names = [e["name"] for e in envs]
    if len(set(names)) != len(names):
        raise Invalid("duplicate environment")
    for e in envs:
        _check_env_fields(e["name"], e.get("branch"), e.get("health_url"), e.get("port"))
    p = m.Project(name=name, owner_id=user.id, repo_url=repo_url, pipeline=_pipeline(pipeline),
                  webhook_secret=crypto.encrypt(secrets.token_hex(20)))
    p.environments = [m.Environment(**e) for e in envs]
    s.add(p)
    s.commit()
    return p


def update_project(s: Session, p: m.Project, repo_url: str | None, pipeline: list[dict] | None) -> m.Project:
    if repo_url is not None:
        p.repo_url = repo_url
    if pipeline is not None:
        p.pipeline = _pipeline(pipeline)
    s.commit()
    return p


def delete_project(s: Session, p: m.Project) -> None:
    if any(d.status in ACTIVE for d in p.deployments):
        raise Conflict("project has queued or running deployments")
    _delete_logs(s, [d.id for d in p.deployments])
    s.delete(p)
    s.commit()


def _delete_logs(s: Session, deployment_ids: list[int]) -> None:
    if deployment_ids:
        s.query(m.LogLine).filter(m.LogLine.deployment_id.in_(deployment_ids)).delete(synchronize_session=False)


def webhook_secret(p: m.Project) -> str:
    return crypto.decrypt(p.webhook_secret)


# ---- environments -----------------------------------------------------------------------
def add_environment(s: Session, p: m.Project, name: str, branch: str, health_url: str, port: int,
                    auto_deploy: bool) -> m.Environment:
    _check_env_fields(name, branch, health_url, port)
    if repo.get_environment(s, p.id, name):
        raise Conflict(f"environment {name!r} already exists")
    env = m.Environment(project_id=p.id, name=name, branch=branch, health_url=health_url, port=port, auto_deploy=auto_deploy)
    s.add(env)
    s.commit()
    return env


def update_environment(s: Session, env: m.Environment, changes: dict) -> m.Environment:
    _check_env_fields(None, changes.get("branch"), changes.get("health_url"), changes.get("port"))
    for k, v in changes.items():
        setattr(env, k, v)
    s.commit()
    return env


def delete_environment(s: Session, p: m.Project, env: m.Environment) -> None:
    deps = [d for d in p.deployments if d.environment_id == env.id]
    if any(d.status in ACTIVE for d in deps):
        raise Conflict("environment has queued or running deployments")
    _delete_logs(s, [d.id for d in deps])
    for d in deps:
        s.delete(d)
    s.delete(env)
    s.commit()


# ---- variables / secrets (scoped to one environment) --------------------------------------
def put_variable(s: Session, env: m.Environment, key: str, value: str, is_secret: bool) -> None:
    if not _VAR_KEY.match(key) or len(key) > 100:
        raise Invalid("invalid variable name")
    if key.upper().startswith("DEPLOYBOARD_"):
        raise Invalid("the DEPLOYBOARD_ prefix is reserved")
    if "\n" in value or "\r" in value or "\0" in value:
        raise Invalid("value must not contain newlines or NUL characters")
    stored = crypto.encrypt(value) if is_secret else value
    existing = next((v for v in env.variables if v.key == key), None)
    if existing:
        existing.value, existing.is_secret = stored, is_secret
    else:
        env.variables.append(m.Variable(key=key, value=stored, is_secret=is_secret))
    s.commit()


def delete_variable(s: Session, env: m.Environment, key: str) -> None:
    v = next((v for v in env.variables if v.key == key), None)
    if not v:
        raise NotFound("variable not found")
    env.variables.remove(v)
    s.commit()


def masked_variables(env: m.Environment) -> list[dict]:
    return [{"key": v.key, "is_secret": v.is_secret, "value": MASK if v.is_secret else v.value} for v in env.variables]
