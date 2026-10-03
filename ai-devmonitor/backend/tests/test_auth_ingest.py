from tests.conftest import KEY


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_and_roles(client, admin, viewer):
    assert client.post("/auth/login", json={"username": "x", "password": "y"}).status_code == 401
    assert client.get("/auth/me", headers=admin).json()["role"] == "admin"
    assert client.get("/auth/me", headers=viewer).json()["role"] == "viewer"
    assert client.get("/services").status_code == 401
    assert client.post("/detect", headers=viewer).status_code == 403
    assert client.post("/detect", headers=admin).status_code == 200


def test_ingest_requires_key_and_validates(client, viewer):
    body = {"metrics": [{"service": "api", "name": "cpu", "value": 12.5}]}
    assert client.post("/ingest/metrics", json=body).status_code == 401
    assert client.post("/ingest/metrics", json=body, headers=KEY).json() == {"accepted": 1}
    bad = {"logs": [{"service": "api", "level": "LOUD", "message": "x"}]}
    assert client.post("/ingest/logs", json=bad, headers=KEY).status_code == 422
    svcs = client.get("/services", headers=viewer).json()
    assert svcs[0]["name"] == "api" and svcs[0]["metrics"]["cpu"] == 12.5 and svcs[0]["status"] == "green"
