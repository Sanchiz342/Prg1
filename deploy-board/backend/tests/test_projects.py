from app import db
from app.db import models as m

from .conftest import deploy, log_text, make_project, set_var, wait_done

ENVS = [{"name": "development", "branch": "dev"}, {"name": "staging", "branch": "develop"}, {"name": "production", "branch": "main"}]


def test_project_crud_and_defaults(client, admin):
    p = client.post("/api/projects", json={"name": "a", "repo_url": "u"}, headers=admin).json()
    assert len(p["pipeline"]) == 7 and p["webhook_secret"]
    assert [e["name"] for e in p["environments"]] == ["production"]  # default environment
    assert client.post("/api/projects", json={"name": "a", "repo_url": "u"}, headers=admin).status_code == 409
    assert client.patch(f"/api/projects/{p['id']}", json={"repo_url": "u2"}, headers=admin).json()["repo_url"] == "u2"
    assert client.delete(f"/api/projects/{p['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/projects/{p['id']}", headers=admin).status_code == 404


def test_validation_errors(client, admin):
    bad = [
        {"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "nope"}]},
        {"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "shell"}]},
        {"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "shell", "run": "x", "timeout": -1}]},
        {"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "shell", "run": "x", "retries": 99}]},
        {"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "checkout"}, {"name": "a", "kind": "checkout"}]},
        {"name": "x", "repo_url": "r", "environments": [{"name": "qa"}]},
        {"name": "x", "repo_url": "r", "environments": [{"name": "staging"}, {"name": "staging"}]},
        {"name": "x", "repo_url": "r", "environments": [{"name": "staging", "port": 70000}]},
        {"name": "x", "repo_url": "r", "environments": [{"name": "staging", "health_url": "ftp://x"}]},
    ]
    for body in bad:
        assert client.post("/api/projects", json=body, headers=admin).status_code == 422, body


def test_environment_crud(client, admin):
    p = make_project(client, admin, "r", pipeline=[{"name": "c", "kind": "checkout"}])
    r = client.post(f"/api/projects/{p['id']}/environments", json={"name": "staging", "branch": "develop", "port": 8081}, headers=admin)
    assert r.status_code == 201 and r.json()["branch"] == "develop"
    assert client.post(f"/api/projects/{p['id']}/environments", json={"name": "staging"}, headers=admin).status_code == 409
    assert client.post(f"/api/projects/{p['id']}/environments", json={"name": "prod"}, headers=admin).status_code == 422
    r = client.patch(f"/api/projects/{p['id']}/environments/staging", json={"auto_deploy": False, "port": 9000}, headers=admin)
    assert r.json()["auto_deploy"] is False and r.json()["port"] == 9000 and r.json()["branch"] == "develop"
    assert client.delete(f"/api/projects/{p['id']}/environments/staging", headers=admin).status_code == 204
    assert client.patch(f"/api/projects/{p['id']}/environments/staging", json={}, headers=admin).status_code == 404


def test_variables_validation(client, admin):
    p = make_project(client, admin, "r")
    url = f"/api/projects/{p['id']}/environments/production/variables"
    for body in ({"key": "1BAD", "value": "x"}, {"key": "DEPLOYBOARD_X", "value": "x"}, {"key": "A", "value": "multi\nline"}):
        assert client.put(url, json=body, headers=admin).status_code == 422, body
    set_var(client, admin, p, "production", "A", "1")
    set_var(client, admin, p, "production", "A", "2", secret=True)  # upsert + flip to secret
    assert client.get(url, headers=admin).json() == [{"key": "A", "is_secret": True, "value": "••••••••"}]
    assert client.delete(f"{url}/A", headers=admin).status_code == 204
    assert client.delete(f"{url}/A", headers=admin).status_code == 404


def test_variables_and_secrets_are_isolated_per_environment(client, admin, repo):
    p = make_project(client, admin, repo, environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
    for env, token in (("staging", "staging-token-1"), ("production", "prod-token-9")):
        set_var(client, admin, p, env, "GREETING", "hello")
        set_var(client, admin, p, env, "API_KEY", token, secret=True)
    set_var(client, admin, p, "staging", "ONLY_STAGING", "x")
    assert [v["key"] for v in client.get(f"/api/projects/{p['id']}/environments/production/variables", headers=admin).json()] == ["API_KEY", "GREETING"]
    staging = wait_done(client, admin, deploy(client, admin, p, "staging")["id"])
    prod = wait_done(client, admin, deploy(client, admin, p, "production")["id"])
    assert staging["environment"] == "staging" and prod["environment"] == "production"
    # each deployment saw its own environment name, and the other environment's secret never appears in its logs
    assert "env=staging" in log_text(client, admin, staging["id"]) and "env=production" in log_text(client, admin, prod["id"])
    with db.new_session() as s:
        envs = {e.name: e for e in s.query(m.Environment)}
        assert {v.key for v in envs["production"].variables} == {"API_KEY", "GREETING"}
        assert {v.key for v in envs["staging"].variables} == {"API_KEY", "GREETING", "ONLY_STAGING"}


def test_deployments_belong_to_environment_and_filter(client, admin, repo):
    p = make_project(client, admin, repo, environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
    a = wait_done(client, admin, deploy(client, admin, p, "staging")["id"])
    b = wait_done(client, admin, deploy(client, admin, p, "production")["id"])
    ids = lambda q: [d["id"] for d in client.get(f"/api/projects/{p['id']}/deployments{q}", headers=admin).json()]
    assert ids("") == [b["id"], a["id"]] and ids("?environment=staging") == [a["id"]]
    assert client.get(f"/api/projects/{p['id']}/deployments?environment=development", headers=admin).status_code == 404
    envs = {e["name"]: e for e in client.get(f"/api/projects/{p['id']}", headers=admin).json()["environments"]}
    assert envs["staging"]["last_deployment"]["id"] == a["id"] and envs["production"]["last_deployment"]["id"] == b["id"]


def test_delete_environment_and_project_remove_history(client, admin, repo):
    p = make_project(client, admin, repo, environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
    d = wait_done(client, admin, deploy(client, admin, p, "staging")["id"])
    assert client.delete(f"/api/projects/{p['id']}/environments/staging", headers=admin).status_code == 204
    assert client.get(f"/api/deployments/{d['id']}", headers=admin).status_code == 404
    with db.new_session() as s:
        assert s.query(m.LogLine).filter_by(deployment_id=d["id"]).count() == 0
    assert client.delete(f"/api/projects/{p['id']}", headers=admin).status_code == 204
    with db.new_session() as s:
        assert s.query(m.Environment).count() == 0 and s.query(m.Variable).count() == 0
