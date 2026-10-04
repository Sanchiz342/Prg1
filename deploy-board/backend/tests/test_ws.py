import threading
import time

from .conftest import deploy, make_project, set_var, wait_done

STREAM_PIPE = [{"name": "checkout", "kind": "checkout", "retry_delay": 0},
               {"name": "talk", "kind": "shell", "run": "echo one; sleep 1; echo two; sleep 1; echo three"}]


def token(headers) -> str:
    return headers["Authorization"].split()[1]


def collect(ws, until_status=("SUCCESS", "FAILED")):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == "deployment" and ev["status"] in until_status:
            return events


def test_live_stream_has_all_lines_in_order_without_duplicates(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=STREAM_PIPE)
    d = deploy(client, admin, p)
    with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
        events = collect(ws)
    logs = [e for e in events if e["type"] == "log"]
    ids = [e["id"] for e in logs]
    assert ids == sorted(set(ids))  # strictly increasing: no duplicates, no reordering
    lines = [e["line"] for e in logs]
    assert [l for l in lines if l in ("one", "two", "three")] == ["one", "two", "three"]
    assert events[-1]["type"] == "deployment" and events[-1]["status"] == "SUCCESS"
    stored = [l["line"] for l in client.get(f"/api/deployments/{d['id']}/logs", headers=admin).json()]
    assert lines == stored  # the stream equals the stored log


def test_lines_arrive_while_the_deployment_is_still_running(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=STREAM_PIPE)
    d = deploy(client, admin, p)
    with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
        while True:
            ev = ws.receive_json()
            if ev["type"] == "log" and ev["line"] == "one":
                break
        status = client.get(f"/api/deployments/{d['id']}", headers=admin).json()["status"]
        assert status == "RUNNING"  # "one" was delivered live, long before the run ended
        collect(ws)


def test_stage_events_are_streamed(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=STREAM_PIPE)
    d = deploy(client, admin, p)
    with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
        events = collect(ws)
    seen = [(e["name"], e["status"]) for e in events if e["type"] == "stage"]
    assert ("talk", "RUNNING") in seen and ("talk", "SUCCESS") in seen


def test_finished_deployment_replays_logs_then_status(client, admin, repo):
    p = make_project(client, admin, repo)
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
        events = collect(ws)
    stored = [l["line"] for l in client.get(f"/api/deployments/{d['id']}/logs", headers=admin).json()]
    assert [e["line"] for e in events if e["type"] == "log"] == stored
    assert events[-1]["status"] == d["status"]


def test_failed_deployment_streams_failure(client, admin, repo):
    p = make_project(client, admin, repo)  # GREETING missing -> fails
    d = deploy(client, admin, p)
    with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
        events = collect(ws)
    assert events[-1]["status"] == "FAILED"


def test_two_viewers_get_the_same_stream(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=STREAM_PIPE)
    d = deploy(client, admin, p)
    results = []

    def watch():
        with client.websocket_connect(f"/ws/deployments/{d['id']}?token={token(admin)}") as ws:
            results.append([e["line"] for e in collect(ws) if e["type"] == "log"])

    threads = [threading.Thread(target=watch) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert len(results) == 2 and results[0] == results[1] and "three" in results[0]


def test_unknown_deployment_is_rejected(client, admin):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/deployments/999?token={token(admin)}") as ws:
            ws.receive_json()
