from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import schemas
from ..db import models as m
from ..db import repositories as repo
from ..jobs import JobQueue
from ..services import deployments, projects
from .deps import current_user, get_queue, get_session

router = APIRouter(prefix="/api", tags=["projects"])


def project_out(p: m.Project) -> schemas.ProjectOut:
    out = schemas.ProjectOut.model_validate(p)
    out.webhook_secret = projects.webhook_secret(p)
    latest: dict[int, m.Deployment] = {}
    for d in p.deployments:  # newest first
        latest.setdefault(d.environment_id, d)
    for env_model, env_out in zip(p.environments, out.environments):
        d = latest.get(env_model.id)
        env_out.last_deployment = schemas.DeploymentOut.model_validate(d) if d else None
    return out


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    return [project_out(p) for p in projects.list_projects(s, user)]


@router.post("/projects", response_model=schemas.ProjectOut, status_code=201)
def create_project(body: schemas.ProjectIn, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    p = projects.create_project(s, user, body.name, body.repo_url, body.pipeline, [e.model_dump() for e in body.environments])
    return project_out(p)


@router.get("/projects/{pid}", response_model=schemas.ProjectOut)
def get_project(pid: int, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    return project_out(projects.get_project(s, user, pid))


@router.patch("/projects/{pid}", response_model=schemas.ProjectOut)
def patch_project(pid: int, body: schemas.ProjectPatch, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    p = projects.get_project(s, user, pid)
    return project_out(projects.update_project(s, p, body.repo_url, body.pipeline))


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    projects.delete_project(s, projects.get_project(s, user, pid))


# ---- environments ----
@router.post("/projects/{pid}/environments", response_model=schemas.EnvOut, status_code=201)
def add_environment(pid: int, body: schemas.EnvIn, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    p = projects.get_project(s, user, pid)
    return projects.add_environment(s, p, **body.model_dump())


@router.patch("/projects/{pid}/environments/{name}", response_model=schemas.EnvOut)
def patch_environment(pid: int, name: str, body: schemas.EnvPatch, user: m.User = Depends(current_user),
                      s: Session = Depends(get_session)):
    env = projects.get_environment(s, projects.get_project(s, user, pid), name)
    return projects.update_environment(s, env, body.model_dump(exclude_unset=True))


@router.delete("/projects/{pid}/environments/{name}", status_code=204)
def delete_environment(pid: int, name: str, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    p = projects.get_project(s, user, pid)
    projects.delete_environment(s, p, projects.get_environment(s, p, name))


# ---- variables / secrets (per environment) ----
@router.get("/projects/{pid}/environments/{name}/variables", response_model=list[schemas.VariableOut])
def list_variables(pid: int, name: str, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    env = projects.get_environment(s, projects.get_project(s, user, pid), name)
    return projects.masked_variables(env)


@router.put("/projects/{pid}/environments/{name}/variables", status_code=204)
def put_variable(pid: int, name: str, body: schemas.VariableIn, user: m.User = Depends(current_user),
                 s: Session = Depends(get_session)):
    env = projects.get_environment(s, projects.get_project(s, user, pid), name)
    projects.put_variable(s, env, body.key, body.value, body.is_secret)


@router.delete("/projects/{pid}/environments/{name}/variables/{key}", status_code=204)
def delete_variable(pid: int, name: str, key: str, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    env = projects.get_environment(s, projects.get_project(s, user, pid), name)
    projects.delete_variable(s, env, key)


# ---- deployments ----
@router.post("/projects/{pid}/environments/{name}/deploy", response_model=schemas.DeploymentOut, status_code=202)
def deploy(pid: int, name: str, user: m.User = Depends(current_user), s: Session = Depends(get_session),
           queue: JobQueue = Depends(get_queue)):
    p = projects.get_project(s, user, pid)
    return deployments.create_deployment(s, queue, p, projects.get_environment(s, p, name), "manual")


@router.get("/projects/{pid}/deployments", response_model=list[schemas.DeploymentOut])
def list_deployments(pid: int, environment: str | None = None, limit: int = 50, user: m.User = Depends(current_user),
                     s: Session = Depends(get_session)):
    p = projects.get_project(s, user, pid)
    env_id = projects.get_environment(s, p, environment).id if environment else None
    return repo.list_deployments(s, p.id, env_id, max(1, min(limit, 200)))


@router.get("/deployments/{did}", response_model=schemas.DeploymentOut)
def get_deployment(did: int, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    return projects.get_deployment(s, user, did)


@router.get("/deployments/{did}/logs", response_model=list[schemas.LogOut])
def get_logs(did: int, after: int = 0, user: m.User = Depends(current_user), s: Session = Depends(get_session)):
    projects.get_deployment(s, user, did)
    return repo.logs_after(s, did, after)


@router.post("/deployments/{did}/rollback", response_model=schemas.DeploymentOut, status_code=202)
def rollback(did: int, user: m.User = Depends(current_user), s: Session = Depends(get_session),
             queue: JobQueue = Depends(get_queue)):
    return deployments.rollback(s, queue, projects.get_deployment(s, user, did))
