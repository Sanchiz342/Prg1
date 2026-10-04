import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..db import models as m
from ..db import repositories as repo
from ..errors import Conflict
from ..jobs import JobQueue

log = logging.getLogger("deployboard.deployments")


def create_deployment(s: Session, queue: JobQueue, project: m.Project, env: m.Environment, trigger: str, **fields) -> m.Deployment:
    """Persist a QUEUED deployment, then hand it to the queue. Never executes anything itself."""
    fields.setdefault("branch", env.branch)
    d = m.Deployment(project_id=project.id, environment_id=env.id, trigger=trigger, **fields)
    s.add(d)
    s.commit()
    queue.enqueue(d.id)
    return d


def rollback(s: Session, queue: JobQueue, d: m.Deployment) -> m.Deployment:
    """Redeploy a previously built image of the *same environment*: `d` itself if it succeeded,
    otherwise the latest earlier successful deployment."""
    target = d if d.status == "SUCCESS" and d.image else repo.latest_successful_before(s, d.environment_id, d.id)
    if not target:
        raise Conflict("no earlier successful deployment with an image to roll back to")
    return create_deployment(s, queue, d.project, d.environment, "rollback", rollback_of=target.id, image=target.image,
                             commit=target.commit, branch=target.branch, author=target.author)


def finish_failed(s: Session, d: m.Deployment, message: str) -> None:
    """Mark a deployment FAILED and close any stage left open (RUNNING -> FAILED, PENDING -> SKIPPED)."""
    for st in d.stages:
        if st.status == "RUNNING":
            st.status, st.finished_at = "FAILED", m.now()
        elif st.status == "PENDING":
            st.status = "SKIPPED"
    d.status, d.error, d.finished_at, d.lease_expires_at = "FAILED", message, m.now(), None
    s.commit()


def requeue_expired(s: Session, queue: JobQueue) -> list[int]:
    """Jobs whose worker stopped heartbeating (crash / restart) go back to the queue, up to max_attempts."""
    requeued: list[int] = []
    for d in repo.expired_running(s, m.now()):
        if d.attempts >= settings.max_attempts:
            finish_failed(s, d, f"worker lost; gave up after {d.attempts} attempts")
            log.warning("deployment %s failed: worker lost %s times", d.id, d.attempts)
            continue
        repo.reset_stages(s, d)
        d.status, d.lease_expires_at, d.error = "QUEUED", None, ""
        s.commit()
        queue.enqueue(d.id)
        requeued.append(d.id)
    return requeued


def recover(s: Session, queue: JobQueue) -> list[int]:
    """Startup recovery: re-enqueue everything still QUEUED (the queue may have been lost) and
    re-queue expired RUNNING jobs. Duplicates are harmless because claiming is atomic."""
    ids = requeue_expired(s, queue)
    for did in repo.queued_ids(s):
        queue.enqueue(did)
        if did not in ids:
            ids.append(did)
    return ids
