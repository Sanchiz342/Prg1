import pytest

from app import db
from app.config import settings
from app.db import models as m
from app.pipeline.engine import plan_stages

from .conftest import make_project


@pytest.fixture
def manual(monkeypatch):
    monkeypatch.setattr(settings, "embedded_workers", False)  # rollback jobs stay QUEUED; we only test target selection


def seed(project_id, env_name, rows):
    with db.new_session() as s:
        env = s.query(m.Environment).filter_by(project_id=project_id, name=env_name).one()
        ids = []
        for status, image in rows:
            d = m.Deployment(project_id=project_id, environment_id=env.id, status=status, image=image, commit=image, branch="main")
            s.add(d)
            s.commit()
            ids.append(d.id)
        return ids


def test_rollback_targets_latest_earlier_success(manual, client, admin):
    p = make_project(client, admin, "r")
    a, b, failed = seed(p["id"], "production", [("SUCCESS", "shop:aaa"), ("SUCCESS", "shop:bbb"), ("FAILED", "")])
    rb = client.post(f"/api/deployments/{failed}/rollback", headers=admin)
    assert rb.status_code == 202
    j = rb.json()
    assert (j["image"], j["trigger"], j["rollback_of"], j["status"], j["environment"]) == ("shop:bbb", "rollback", b, "QUEUED", "production")
    # a successful deployment rolls back to itself
    assert client.post(f"/api/deployments/{a}/rollback", headers=admin).json()["image"] == "shop:aaa"


def test_rollback_without_candidate_is_conflict(manual, client, admin):
    p = make_project(client, admin, "r")
    (failed,) = seed(p["id"], "production", [("FAILED", "")])
    assert client.post(f"/api/deployments/{failed}/rollback", headers=admin).status_code == 409
    (no_image,) = seed(p["id"], "production", [("SUCCESS", "")])  # succeeded but built no image
    assert client.post(f"/api/deployments/{no_image}/rollback", headers=admin).status_code == 409


def test_rollback_never_crosses_environments(manual, client, admin):
    p = make_project(client, admin, "r", environments=[{"name": "staging"}, {"name": "production"}])
    seed(p["id"], "staging", [("SUCCESS", "shop-staging:111")])
    (prod_failed,) = seed(p["id"], "production", [("FAILED", "")])
    assert client.post(f"/api/deployments/{prod_failed}/rollback", headers=admin).status_code == 409
    (prod_ok,) = seed(p["id"], "production", [("SUCCESS", "shop-production:222")])
    (prod_failed2,) = seed(p["id"], "production", [("FAILED", "")])
    j = client.post(f"/api/deployments/{prod_failed2}/rollback", headers=admin).json()
    assert j["image"] == "shop-production:222" and j["rollback_of"] == prod_ok and j["environment"] == "production"


def test_rollback_runs_only_deploy_and_healthcheck(manual, client, admin):
    p = make_project(client, admin, "r")
    (ok,) = seed(p["id"], "production", [("SUCCESS", "shop:aaa")])
    j = client.post(f"/api/deployments/{ok}/rollback", headers=admin).json()
    with db.new_session() as s:
        project = s.get(m.Project, p["id"])
        assert [x["name"] for x in plan_stages(project, "rollback")] == ["deploy", "healthcheck"]
        assert [x["kind"] for x in plan_stages(project, "rollback")] == ["docker_run", "healthcheck"]
        assert s.get(m.Deployment, j["id"]).image == "shop:aaa"  # no build: the stored image is reused
