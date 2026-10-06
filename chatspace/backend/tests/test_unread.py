import time

from tests.test_realtime import connect, recv_until


def unread(api, who, wid):
    r = api.call("GET", f"/workspaces/{wid}/unread", who)
    assert r.status_code == 200, r.text
    return r.json()["channels"]


def post(api, who, channel, text, reply_to=None):
    r = api.call("POST", f"/channels/{channel['id']}/messages", who, json={"content": text, "reply_to": reply_to})
    assert r.status_code == 201, r.text
    return r.json()


def test_counts_other_peoples_messages_only_and_marking_read_clears_them(api, team):
    t, wid, g = team, team["ws"]["id"], team["general"]
    assert unread(api, t["bob"], wid) == {}
    for i in range(3):
        post(api, t["alice"], g, f"m{i}")
    post(api, t["bob"], g, "mine")                                   # own messages never count
    assert unread(api, t["bob"], wid) == {g["id"]: {"unread": 3, "mentions": 0}}
    assert unread(api, t["alice"], wid) == {g["id"]: {"unread": 1, "mentions": 0}}
    assert api.call("POST", f"/channels/{g['id']}/read", t["bob"]).status_code == 204
    assert unread(api, t["bob"], wid) == {}
    post(api, t["alice"], g, "again")
    assert unread(api, t["bob"], wid)[g["id"]]["unread"] == 1


def test_replies_count_deleted_messages_do_not(api, team):
    t, wid, g = team, team["ws"]["id"], team["general"]
    parent = post(api, t["alice"], g, "parent")
    post(api, t["alice"], g, "reply", reply_to=parent["id"])
    doomed = post(api, t["alice"], g, "oops")
    assert unread(api, t["bob"], wid)[g["id"]]["unread"] == 3
    api.call("DELETE", f"/messages/{doomed['id']}", t["alice"])
    assert unread(api, t["bob"], wid)[g["id"]]["unread"] == 2


def test_marker_never_moves_backwards_and_is_per_user(api, team):
    t, wid, g = team, team["ws"]["id"], team["general"]
    post(api, t["alice"], g, "one")
    api.call("POST", f"/channels/{g['id']}/read", t["bob"])
    api.call("POST", f"/channels/{g['id']}/read", t["bob"])            # idempotent
    post(api, t["alice"], g, "two")
    assert unread(api, t["bob"], wid)[g["id"]]["unread"] == 1
    assert unread(api, t["carol"], wid)[g["id"]]["unread"] == 2         # carol never read anything


def test_mentions_are_counted_and_cleared_by_reading(api, team):
    t, wid, g = team, team["ws"]["id"], team["general"]
    post(api, t["alice"], g, "hello @bob")
    post(api, t["alice"], g, "plain")
    assert unread(api, t["bob"], wid)[g["id"]] == {"unread": 2, "mentions": 1}
    assert [n["kind"] for n in api.call("GET", "/notifications?unread=true", t["bob"]).json()].count("mention") == 1
    api.call("POST", f"/channels/{g['id']}/read", t["bob"])
    assert unread(api, t["bob"], wid) == {}
    assert "mention" not in [n["kind"] for n in api.call("GET", "/notifications?unread=true", t["bob"]).json()]


def test_private_channels_are_invisible_to_non_members(api, team):
    t, wid, s = team, team["ws"]["id"], team["secret"]
    post(api, t["alice"], s, "classified")
    assert unread(api, t["bob"], wid) == {}                                          # not even a key
    assert api.call("POST", f"/channels/{s['id']}/read", t["bob"]).status_code == 404
    api.call("POST", f"/channels/{s['id']}/members", t["alice"], json={"user_id": t["bob"]["id"]})
    assert unread(api, t["bob"], wid) == {}                                          # joined: history is not unread
    post(api, t["alice"], s, "after")
    assert unread(api, t["bob"], wid)[s["id"]]["unread"] == 1


def test_new_workspace_member_starts_caught_up_and_new_channels_start_unread(api, team):
    t, wid, g = team, team["ws"]["id"], team["general"]
    for i in range(5):
        post(api, t["alice"], g, f"old {i}")
    dave = api.user("dave")
    api.call("POST", f"/workspaces/{wid}/members", t["alice"], json={"email": "dave@x.io"})
    assert unread(api, dave, wid) == {}                                              # 5 old messages are history
    post(api, t["alice"], g, "new")
    assert unread(api, dave, wid)[g["id"]]["unread"] == 1
    dev = api.call("POST", f"/workspaces/{wid}/channels", t["alice"], json={"name": "dev"}).json()
    post(api, t["alice"], dev, "first in dev")
    assert unread(api, dave, wid)[dev["id"]]["unread"] == 1                          # everyone else sees a new channel's messages
    assert dev["id"] not in unread(api, t["alice"], wid)                             # creator wrote them


def test_unread_requires_membership(api, team):
    outsider = api.user("eve")
    assert api.call("GET", f"/workspaces/{team['ws']['id']}/unread", outsider).status_code == 404


# ---- live events

def test_activity_ping_reaches_members_who_have_not_opened_the_channel(api, team, client):
    t, g = team, team["general"]
    ws_b, bob = connect(client, t["bob"])                     # bob never subscribes to #general
    try:
        post(api, t["alice"], g, "secret text must not leak")
        ev = recv_until(bob, "channel.activity")
        assert ev["channel_id"] == g["id"] and ev["author_id"] == t["alice"]["id"]
        assert "content" not in ev and "message" not in ev
    finally:
        ws_b.__exit__(None, None, None)


def test_private_channel_activity_goes_only_to_its_members(api, team, client):
    t = team
    ws_b, bob = connect(client, t["bob"])
    ws_a, alice = connect(client, t["alice"])
    try:
        post(api, t["alice"], team["secret"], "private news")
        assert recv_until(alice, "channel.activity")["channel_id"] == team["secret"]["id"]
        post(api, t["alice"], team["general"], "public news")
        got = recv_until(bob, "channel.activity")             # the first activity bob ever sees is the public one
        assert got["channel_id"] == team["general"]["id"]
    finally:
        ws_b.__exit__(None, None, None)
        ws_a.__exit__(None, None, None)


def test_reading_in_one_tab_clears_the_badge_in_the_others(api, team, client):
    t, g = team, team["general"]
    ws1, tab1 = connect(client, t["bob"])
    ws2, tab2 = connect(client, t["bob"])
    try:
        api.call("POST", f"/channels/{g['id']}/read", t["bob"])
        assert recv_until(tab1, "channel.read")["channel_id"] == g["id"]
        assert recv_until(tab2, "channel.read")["channel_id"] == g["id"]
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)
