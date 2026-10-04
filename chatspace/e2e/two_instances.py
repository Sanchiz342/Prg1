import asyncio, json, httpx, websockets
A, B = "http://localhost:8000", "http://localhost:8001"
def ok(c, m): print("ok  " if c else "FAIL", m); return c
async def main():
    good = True
    async with httpx.AsyncClient(timeout=10) as h:
        for name, base in (("instance1", A), ("instance2", B)):
            r = await h.get(base + "/api/health"); good &= ok(r.json() == {"database": "ok", "redis": "ok", "status": "ok"}, f"{name} health")
        reg = (await h.post(A + "/api/auth/register", json={"username": "carol", "email": "carol@x.io", "password": "password123"})).json()
        tok = reg["access_token"]; H = {"Authorization": f"Bearer {tok}"}
        good &= ok((await h.get(B + "/api/auth/me", headers=H)).json()["username"] == "carol", "token minted by instance1 accepted by instance2 (shared secret + DB)")
        ws = (await h.post(B + "/api/workspaces", headers=H, json={"name": "Direct"})).json()
        ch = (await h.get(A + f"/api/workspaces/{ws['id']}/channels", headers=H)).json()[0]
        good &= ok(ch["name"] == "general", "workspace created on instance2 is visible on instance1")
        # websocket on instance 1; REST write on instance 2 -> frame arrives (Redis Pub/Sub)
        async with websockets.connect(f"ws://localhost:8000/ws?token={tok}") as sock:
            await sock.recv()  # ready
            await sock.send(json.dumps({"type": "subscribe", "channel_id": ch["id"]}))
            while json.loads(await sock.recv())["type"] != "subscribed": pass
            await h.post(B + f"/api/channels/{ch['id']}/messages", headers=H, json={"content": "written via instance 2"})
            got = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(sock.recv(), 5))
                if m["type"] == "message.created": got = m; break
            good &= ok(got and got["message"]["content"] == "written via instance 2", "ws on :8000 received message created through :8001")
        # and the other direction
        async with websockets.connect(f"ws://localhost:8001/ws?token={tok}") as sock:
            await sock.recv()
            await sock.send(json.dumps({"type": "subscribe", "channel_id": ch["id"]}))
            while json.loads(await sock.recv())["type"] != "subscribed": pass
            await h.post(A + f"/api/channels/{ch['id']}/messages", headers=H, json={"content": "written via instance 1"})
            got = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(sock.recv(), 5))
                if m["type"] == "message.created": got = m; break
            good &= ok(got and got["message"]["content"] == "written via instance 1", "ws on :8001 received message created through :8000")
        # unauthenticated websocket is rejected by each instance
        for base in ("ws://localhost:8000", "ws://localhost:8001"):
            async with websockets.connect(base + "/ws?token=bad") as s:
                try:
                    await asyncio.wait_for(s.recv(), 5)
                    good &= ok(False, f"{base} sent data to a bad token")
                except websockets.exceptions.ConnectionClosed as e:
                    good &= ok(e.rcvd and e.rcvd.code == 4401, f"{base} closes bad token with 4401")
        # FTS on each instance returns the same result
        for name, base in (("instance1", A), ("instance2", B)):
            r = (await h.get(base + f"/api/workspaces/{ws['id']}/search", headers=H, params={"q": "writ via"})).json()
            good &= ok(len(r) == 2, f"{name} FTS finds both messages ({len(r)})")
    print("\nALL PASSED" if good else "\nSOME FAILED")
asyncio.run(main())
