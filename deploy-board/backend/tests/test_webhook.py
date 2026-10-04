import json

from .conftest import make_project, sign, wait_done

ENVS = [{"name": "staging", "branch": "develop"}, {"name": "production", "branch": "main"}]
HDR = {"X-GitHub-Event": "push", "content-type": "application/json"}


def push(client, project, repo_url, ref="refs/heads/main", secret=None, **extra):
    payload = {"ref": ref, "after": "", "repository": {"clone_url": repo_url}, "head_commit": {"author": {"name": "Alice"}}, **extra}
    body = json.dumps(payload).encode()
    headers = dict(HDR)
    if secret is not False:
        headers["X-Hub-Signature-256"] = sign(secret or project["webhook_secret"], body)
    return client.post("/api/webhooks/github", content=body, headers=headers)


def test_valid_signature_deploys_matching_branch(client, admin, repo):
    p = make_project(client, admin, repo, environments=ENVS)
    r = push(client, p, str(repo.path))
    assert r.status_code == 200 and len(r.json()["deployments"]) == 1
    d = wait_done(client, admin, r.json()["deployments"][0])
    assert d["trigger"] == "webhook" and d["author"] == "Alice" and d["environment"] == "production" and d["branch"] == "main"


def test_signature_validation(client, admin, repo):
    p = make_project(client, admin, repo, environments=ENVS)
    assert push(client, p, str(repo.path), secret=False).status_code == 401  # missing header
    assert push(client, p, str(repo.path), secret="wrong-secret").status_code == 401
    body = json.dumps({"ref": "refs/heads/main", "repository": {"clone_url": str(repo.path)}}).encode()
    good = sign(p["webhook_secret"], body)
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    for bad in ("sha256=", "sha1=abc", "abc", flipped):
        r = client.post("/api/webhooks/github", content=body, headers={**HDR, "X-Hub-Signature-256": bad})
        assert r.status_code == 401, bad
    # a valid signature over a *different* body must not authorise this body
    other = sign(p["webhook_secret"], b'{"x":1}')
    assert client.post("/api/webhooks/github", content=body, headers={**HDR, "X-Hub-Signature-256": other}).status_code == 401
    # nothing was queued by any rejected request
    assert client.get(f"/api/projects/{p['id']}/deployments", headers=admin).json() == []


def test_secret_of_another_project_is_not_accepted(client, admin, repo, tmp_path):
    from .conftest import GitRepo
    other_repo = GitRepo(tmp_path / "other")
    a = make_project(client, admin, repo, name="a")
    b = make_project(client, admin, other_repo, name="b")
    assert push(client, a, str(repo.path), secret=b["webhook_secret"]).status_code == 401
    assert push(client, b, str(other_repo.path)).status_code == 200


def test_branch_filtering(client, admin, repo):
    p = make_project(client, admin, repo, environments=ENVS + [{"name": "development", "branch": "main", "auto_deploy": False}])
    r = push(client, p, str(repo.path), ref="refs/heads/develop")
    assert [wait_done(client, admin, i)["environment"] for i in r.json()["deployments"]] == ["staging"]
    r = push(client, p, str(repo.path), ref="refs/heads/feature/x")
    assert "ignored" in r.json() and "feature/x" in r.json()["ignored"]
    r = push(client, p, str(repo.path), ref="refs/tags/v1")
    assert r.json() == {"ignored": "not a branch push"}
    r = push(client, p, str(repo.path), ref="refs/heads/main", deleted=True)
    assert "ignored" in r.json()
    # `main` maps to production (auto) and development (auto_deploy off): only production deploys
    r = push(client, p, str(repo.path), ref="refs/heads/main")
    assert [wait_done(client, admin, i)["environment"] for i in r.json()["deployments"]] == ["production"]


def test_one_push_can_deploy_several_environments(client, admin, repo):
    p = make_project(client, admin, repo, environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
    ids = push(client, p, str(repo.path)).json()["deployments"]
    assert sorted(wait_done(client, admin, i)["environment"] for i in ids) == ["production", "staging"]


def test_events_and_errors(client, admin, repo):
    p = make_project(client, admin, repo)
    assert client.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"}).json() == {"ignored": "ping"}
    assert "ignored" in client.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "issues"}).json()
    assert client.post("/api/webhooks/github", content=b"not json", headers=HDR).status_code == 422
    assert client.post("/api/webhooks/github", content=b"[1]", headers=HDR).status_code == 422
    assert push(client, p, "https://github.com/someone/else").status_code == 404  # unknown repository


def test_repository_url_forms_are_matched(client, admin, repo):
    p = make_project(client, admin, "https://github.com/User/Shop-API.git", pipeline=[{"name": "c", "kind": "checkout", "retries": 0}])
    for repo_fields in ({"clone_url": "https://github.com/user/shop-api.git"}, {"html_url": "https://github.com/user/shop-api"},
                        {"ssh_url": "git@github.com:user/shop-api.git"}):
        body = json.dumps({"ref": "refs/heads/main", "repository": repo_fields}).encode()
        r = client.post("/api/webhooks/github", content=body, headers={**HDR, "X-Hub-Signature-256": sign(p["webhook_secret"], body)})
        assert r.status_code == 200 and r.json()["deployments"], repo_fields
