import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import github
from ..db import models as m
from ..db import repositories as repo
from ..errors import Invalid, NotFound, Unauthorized
from ..jobs import JobQueue
from . import deployments, projects


@dataclass
class WebhookResult:
    deployments: list[int] = field(default_factory=list)
    ignored: str = ""


def handle_github(s: Session, queue: JobQueue, event: str, body: bytes, signature: str | None) -> WebhookResult:
    if event == "ping":
        return WebhookResult(ignored="ping")
    if event != "push":
        return WebhookResult(ignored=f"event {event!r}")
    try:
        payload = json.loads(body)
    except ValueError:
        raise Invalid("invalid JSON")
    if not isinstance(payload, dict):
        raise Invalid("invalid payload")
    repository = payload.get("repository") or {}
    candidates = [p for p in repo.list_projects(s) if github.repo_matches(p.repo_url, repository)]
    if not candidates:
        raise NotFound("no project for this repository")
    authed = [p for p in candidates if github.verify_signature(projects.webhook_secret(p), body, signature)]
    if not authed:
        raise Unauthorized("invalid signature")
    push = github.parse_push(payload)
    if not push:
        return WebhookResult(ignored="not a branch push")
    result = WebhookResult()
    for p in authed:
        for env in p.environments:
            if env.auto_deploy and env.branch == push["branch"]:
                result.deployments.append(deployments.create_deployment(s, queue, p, env, "webhook", **push).id)
    if not result.deployments:
        result.ignored = f"branch {push['branch']!r} is not deployed by any environment"
    return result
