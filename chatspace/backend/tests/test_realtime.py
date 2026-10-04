import asyncio
import time

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from starlette.websockets import WebSocketDisconnect

from app.realtime.hub import Hub
from app.realtime.presence import Presence


def recv_until(ws, type_, limit=10):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == type_:
            return m
    raise AssertionError(f"no {type_} frame")


def connect(client, who):
    ws = client.websocket_connect(f"/ws?token={who['token']}")
    sock = ws.__enter__()
    assert sock.receive_json()["type"] == "ready"
    return ws, sock


def test_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws?token=bad"):
            pass
    assert e.value.code == 4401


def test_broadcast_and_private_channel_acl(api, team, client):
    t = team
    ws_b, bob = connect(client, t["bob"])
    ws_a, alice = connect(client, t["alice"])
    try:
        bob.send_json({"type": "subscribe", "channel_id": t["general"]["id"]})
        recv_until(bob, "subscribed")
        # bob may not listen to alice's private channel
        bob.send_json({"type": "subscribe", "channel_id": t["secret"]["id"]})
        assert recv_until(bob, "error")["code"] == "forbidden"
        alice.send_json({"type": "subscribe", "channel_id": t["secret"]["id"]})
        recv_until(alice, "subscribed")

        r = api.call("POST", f"/channels/{t['general']['id']}/messages", t["carol"], json={"content": "Hello"})
        m = recv_until(bob, "message.created")["message"]
        assert m["id"] == r.json()["id"] and m["content"] == "Hello"

        api.call("POST", f"/channels/{t['secret']['id']}/messages", t["alice"], json={"content": "classified"})
        got = recv_until(alice, "message.created")["message"]
        assert got["content"] == "classified"
        # bob received nothing for the secret channel: next frame he gets is the following public one
        api.call("POST", f"/channels/{t['general']['id']}/messages", t["carol"], json={"content": "next"})
        assert recv_until(bob, "message.created")["message"]["content"] == "next"
    finally:
        ws_b.__exit__(None, None, None)
        ws_a.__exit__(None, None, None)


def test_edit_delete_reaction_events(api, team, client):
    t, gid = team, team["general"]["id"]
    ws_b, bob = connect(client, t["bob"])
    try:
        bob.send_json({"type": "subscribe", "channel_id": gid})
        recv_until(bob, "subscribed")
        mid = api.call("POST", f"/channels/{gid}/messages", t["alice"], json={"content": "a"}).json()["id"]
        recv_until(bob, "message.created")
        api.call("PATCH", f"/messages/{mid}", t["alice"], json={"content": "b"})
        assert recv_until(bob, "message.updated")["message"]["content"] == "b"
        api.call("PUT", f"/messages/{mid}/reactions/🔥", t["carol"])
        assert recv_until(bob, "reaction.added")["reaction"] == "🔥"
        api.call("DELETE", f"/messages/{mid}", t["alice"])
        assert recv_until(bob, "message.deleted")["message_id"] == mid
    finally:
        ws_b.__exit__(None, None, None)


def test_typing_and_presence(api, team, client):
    t, gid = team, team["general"]["id"]
    ws_a, alice = connect(client, t["alice"])
    try:
        alice.send_json({"type": "subscribe", "channel_id": gid})
        recv_until(alice, "subscribed")
        # bob comes online -> alice (same workspace) is told
        ws_b, bob = connect(client, t["bob"])
        assert recv_until(alice, "user.online")["user_id"] == t["bob"]["id"]
        members = {m["username"]: m["presence"] for m in api.call("GET", f"/workspaces/{t['ws']['id']}/members", t["alice"]).json()}
        assert members == {"alice": "ONLINE", "bob": "ONLINE", "carol": "OFFLINE"}

        # typing requires a channel subscription, then fans out to the room
        bob.send_json({"type": "typing.started", "channel_id": gid})
        assert recv_until(bob, "error")["code"] == "not_subscribed"
        bob.send_json({"type": "subscribe", "channel_id": gid})
        recv_until(bob, "subscribed")
        bob.send_json({"type": "typing.started", "channel_id": gid})
        ev = recv_until(alice, "typing.started")
        assert ev["user_id"] == t["bob"]["id"]
        ws_b.__exit__(None, None, None)
        assert recv_until(alice, "user.offline")["user_id"] == t["bob"]["id"]
    finally:
        ws_a.__exit__(None, None, None)


def test_notification_pushed_to_user(api, team, client):
    t = team
    ws_c, carol = connect(client, t["carol"])
    try:
        api.call("POST", f"/channels/{t['general']['id']}/messages", t["bob"], json={"content": "ping @carol"})
        n = recv_until(carol, "notification.created")["notification"]
        assert n["kind"] == "mention"
    finally:
        ws_c.__exit__(None, None, None)


class FakeSocket:
    def __init__(self):
        self.frames = []

    async def send_json(self, data):
        self.frames.append(data)


async def test_pubsub_crosses_instances():
    """Two hubs = two backend instances sharing one Redis."""
    server = FakeServer()
    h1, h2 = Hub(FakeAsyncRedis(server=server)), Hub(FakeAsyncRedis(server=server))
    await h1.start(); await h2.start()
    try:
        user_b = FakeSocket()
        h1.join("channel:1", user_b)  # user B is connected to instance 1
        await h2.publish("channel:1", {"type": "message.created", "n": 1})  # user A's message lands on instance 2
        for _ in range(100):
            if user_b.frames:
                break
            await asyncio.sleep(0.02)
        assert user_b.frames == [{"type": "message.created", "n": 1}]
    finally:
        await h1.stop(); await h2.stop()


async def test_presence_ttl_expires():
    r = FakeAsyncRedis(server=FakeServer())
    p = Presence(r, presence_ttl=1, typing_ttl=1)
    await p.set("u1", "AWAY")
    assert await p.get_many(["u1", "u2"]) == {"u1": "AWAY", "u2": "OFFLINE"}
    await asyncio.sleep(1.2)  # no heartbeat -> expires
    assert await p.get_many(["u1"]) == {"u1": "OFFLINE"}
