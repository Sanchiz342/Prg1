// Drives the real UI in Chromium against the docker-compose stack: alice -> instance 1 (:8000), bob -> instance 2 (:8001).
import { chromium } from "playwright-core";
const A = "http://localhost:8000", B = "http://localhost:8001";
const phase = process.argv[2] ?? "flow";
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
let failed = 0;
const ok = (c, msg) => { console.log(c ? "ok  " : "FAIL", msg); if (!c) failed++; };
const mk = async (w = 1200, h = 800) => {
  const c = await browser.newContext({ viewport: { width: w, height: h } });
  const p = await c.newPage();
  p.on("pageerror", (e) => console.log("PAGEERROR", e.message));
  return p;
};
const step = async (msg, fn) => { try { await fn(); ok(true, msg); } catch (e) { ok(false, `${msg} -- ${String(e.message).split("\n")[0]}`); } };
const metric = async (base, name) => {
  const t = await (await fetch(base + "/metrics")).text();
  const m = t.match(new RegExp(`^${name} ([0-9.e+-]+)$`, "m"));
  return m ? Number(m[1]) : 0;
};
const chan = (p, name) => p.locator("button.channel", { hasText: name });
const msg = (p, text) => p.locator("article", { hasText: text }).first();

if (phase === "flow") {
  const a = await mk(), b = await mk();
  const uname = (p) => p.locator(".user-panel span");

  await step("alice registers on instance 1 (:8000)", async () => {
    await a.goto(A);
    await a.getByRole("button", { name: "No account? Register" }).click();
    await a.getByLabel("Username").fill("alice");
    await a.getByLabel("Email").fill("alice@x.io");
    await a.getByLabel("Password").fill("password123");
    await a.getByRole("button", { name: "Create account" }).click();
    await a.getByText("not in any workspace").waitFor();
  });
  await step("bob registers on instance 2 (:8001)", async () => {
    await b.goto(B);
    await b.getByRole("button", { name: "No account? Register" }).click();
    await b.getByLabel("Username").fill("bob");
    await b.getByLabel("Email").fill("bob@x.io");
    await b.getByLabel("Password").fill("password123");
    await b.getByRole("button", { name: "Create account" }).click();
    await b.getByText("not in any workspace").waitFor();
  });
  await step("bob logs out and logs back in (login path, token minted by instance 2)", async () => {
    await b.getByRole("button", { name: "Log out" }).click();
    await b.getByLabel("Email").fill("bob@x.io");
    await b.getByLabel("Password").fill("password123");
    await b.getByRole("button", { name: "Log in" }).click();
    await b.getByText("not in any workspace").waitFor();
  });
  await step("alice creates workspace PRG1", async () => {
    a.once("dialog", (d) => d.accept("PRG1"));
    await a.getByLabel("New workspace").click();
    await a.getByRole("heading", { name: /general/ }).waitFor();
  });
  await step("alice (instance 1) adds bob -> bob (instance 2) sees the workspace live via Redis", async () => {
    await a.getByRole("tab", { name: "Members" }).click();
    await a.getByLabel("Member email").fill("bob@x.io");
    await a.getByRole("button", { name: "Add", exact: true }).click();
    await b.getByRole("heading", { name: /general/ }).waitFor({ timeout: 10000 });
  });
  await step("presence: alice (instance 1) sees bob ONLINE (set by instance 2, stored in Redis)", async () => {
    await a.locator('li:has-text("bob") .dot.ONLINE').waitFor({ timeout: 10000 });
  });

  await step("public + private channels: alice creates #dev and private #secret; bob sees only #dev", async () => {
    const answers = [];
    a.on("dialog", (d) => { const next = answers.shift(); next === false ? d.dismiss() : d.accept(next === true ? undefined : next); });
    for (const [name, priv] of [["dev", false], ["secret", true]]) {
      answers.push(name, priv);               // 1st prompt: channel name, 2nd confirm: "Make it private?"
      await a.getByLabel("New channel").click();
      await chan(a, name).waitFor();
    }
    await chan(b, "dev").waitFor({ timeout: 10000 }); // channel.created via Redis
    ok(await chan(b, "secret").count() === 0, "bob does not see private #secret");
  });

  await step("alice -> bob message crosses instances (Redis Pub/Sub)", async () => {
    await chan(b, "general").click();
    await chan(a, "general").click();
    await a.getByLabel("Message #general").fill("Hello from instance 1");
    await a.keyboard.press("Enter");
    await b.getByText("Hello from instance 1").waitFor({ timeout: 10000 });
  });
  await step("bob -> alice message crosses instances", async () => {
    await b.getByLabel("Message #general").fill("Hi Alice, from instance 2");
    await b.keyboard.press("Enter");
    await a.getByText("Hi Alice, from instance 2").waitFor({ timeout: 10000 });
  });
  await step("typing indicator crosses instances", async () => {
    await b.getByLabel("Message #general").pressSequentially("typ", { delay: 60 });
    await a.getByText("bob is typing…").waitFor({ timeout: 8000 });
    await b.getByLabel("Message #general").fill("");
  });
  await step("reaction from alice propagates to bob", async () => {
    await msg(a, "Hello from instance 1").hover();
    await msg(a, "Hello from instance 1").getByLabel("Add reaction").click();
    await a.locator(".picker button", { hasText: "🔥" }).click();
    await b.getByRole("button", { name: /🔥 1/ }).waitFor({ timeout: 8000 });
  });
  await step("reply from bob (thread) updates alice's reply count", async () => {
    await msg(b, "Hello from instance 1").hover();
    await msg(b, "Hello from instance 1").getByLabel("Reply in thread").click();
    await b.getByLabel("Reply…").fill("@alice looks good");
    await b.keyboard.press("Enter");
    await b.locator(".thread").getByText("@alice looks good").waitFor();
    await a.getByRole("button", { name: /1 reply/ }).waitFor({ timeout: 8000 });
  });
  await step("notifications: alice gets mention/reply push from instance 2", async () => {
    await a.getByRole("tab", { name: /🔔/ }).click();
    await a.getByText(/bob (replied to you|mentioned you)/).first().waitFor({ timeout: 8000 });
  });
  await step("edit propagates", async () => {
    await msg(a, "Hello from instance 1").hover();
    await msg(a, "Hello from instance 1").getByLabel("Edit message").click();
    await a.getByLabel("Edit message").last().fill("Hello (edited)");
    await a.keyboard.press("Enter");
    await b.getByText("Hello (edited)").first().waitFor({ timeout: 8000 });
  });
  await step("delete propagates", async () => {
    await b.locator(".thread").getByLabel("Close thread").click();
    await msg(b, "Hi Alice, from instance 2").hover();
    b.once("dialog", (d) => d.accept());
    await msg(b, "Hi Alice, from instance 2").getByLabel("Delete message").click();
    await a.getByText("This message was deleted").waitFor({ timeout: 8000 });
  });

  await step("FTS via instance 1 with private-channel isolation", async () => {
    await chan(a, "secret").click();
    await a.getByLabel("Message #secret").fill("launch codename falcon (private)");
    await a.keyboard.press("Enter");
    await chan(a, "dev").click();
    await a.getByLabel("Message #dev").fill("falcon public status update");
    await a.keyboard.press("Enter");
    await a.getByText("falcon public status update").waitFor();
    const search = async (p, q) => {
      await p.getByRole("tab", { name: "Search" }).click();
      await p.getByLabel("Search messages").fill(q);
      await p.getByRole("button", { name: "Go" }).click();
      await p.waitForTimeout(800);
      return p.locator(".result").allTextContents();
    };
    const ra = await search(a, "falc");
    const rb = await search(b, "falc");
    ok(ra.length === 2, `alice (member of both) finds 2 results for prefix "falc" -> ${ra.length}`);
    ok(rb.length === 1 && rb[0].includes("public"), `bob (instance 2) finds only the public one -> ${rb.length}`);
  });

  await step("offline catch-up: bob goes offline, alice posts, bob reconnects and sees it", async () => {
    await chan(b, "general").click();
    await b.context().setOffline(true);
    await b.getByText(/Connection lost/).waitFor({ timeout: 15000 }).catch(() => {});
    await chan(a, "general").click();
    await a.getByLabel("Message #general").fill("sent while bob was offline");
    await a.keyboard.press("Enter");
    await a.getByText("sent while bob was offline").waitFor();
    await b.context().setOffline(false);
    await b.getByText("sent while bob was offline").waitFor({ timeout: 20000 });
  });

  // ---- unread counters (run last: they move bob between channels) ----
  const labels = (p) => p.locator("button.channel").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  await step("unread: badge appears on bob (instance 2) for a message alice (instance 1) writes in a channel bob is not viewing", async () => {
    await chan(a, "general").click();
    await chan(b, "dev").click();                                              // bob looks at #dev (reads it)
    await b.getByRole("button", { name: "dev", exact: true }).waitFor();
    await a.getByLabel("Message #general").fill("ping for unread");
    await a.keyboard.press("Enter");
    await b.getByRole("button", { name: "general, 1 unread" }).waitFor({ timeout: 10000 });
    const l = await labels(b);
    ok(!l.some((x) => /unread/.test(x) && !x.startsWith("general")), `no other channel has a badge for bob: ${JSON.stringify(l)}`);
  });
  await step("unread: private channel activity never reaches bob (no badge, no leak)", async () => {
    await chan(a, "secret").click();
    await a.getByLabel("Message #secret").fill("private unread test");
    await a.keyboard.press("Enter");
    await a.getByText("private unread test").waitFor();
    await chan(a, "general").click();
    await b.waitForTimeout(1500);
    const l = await labels(b);
    ok(!l.some((x) => x.startsWith("secret")) && l.includes("general, 1 unread"), `bob's sidebar unchanged: ${JSON.stringify(l)}`);
  });
  await step("unread: mention turns the badge into a mention badge and counts; tab title shows the total", async () => {
    await a.getByLabel("Message #general").fill("@bob unread mention");
    await a.keyboard.press("Enter");
    await b.getByRole("button", { name: "general, 2 unread, 1 mentions" }).waitFor({ timeout: 10000 });
    ok(await b.locator(".pill.mention").count() === 1, "badge is rendered as a mention badge");
    ok((await b.title()).startsWith("(2)"), `tab title: ${await b.title()}`);
  });
  await step("unread: second tab of bob shows it too; reading in tab 1 clears tab 2 (channel.read) and survives a reload", async () => {
    const b2 = await b.context().newPage();
    await b2.goto(B);
    await b2.getByRole("button", { name: "general, 2 unread, 1 mentions" }).waitFor({ timeout: 10000 });
    await chan(b2, "dev").click();                                             // tab 2 looks at #dev
    await chan(b, "general").click();                                          // tab 1 opens #general -> read
    await b2.getByRole("button", { name: "general", exact: true }).waitFor({ timeout: 10000 });
    ok(true, "tab 2 badge cleared by the channel.read event");
    await b.reload();
    await b.locator("button.channel").first().waitFor();
    await b.waitForTimeout(800);
    const l = await labels(b);
    ok(!l.some((x) => /unread/.test(x)), `still cleared after reload (server-side marker): ${JSON.stringify(l)}`);
    await b2.close();
  });
  await step("unread: the author never gets a badge for their own messages", async () => {
    const l = await labels(a);
    ok(!l.some((x) => /unread/.test(x)), `alice's sidebar: ${JSON.stringify(l)}`);
  });

  // each instance really served its own user
  const [wa, wb] = [await metric(A, "chatspace_ws_connections"), await metric(B, "chatspace_ws_connections")];
  ok(wa >= 1 && wb >= 1, `websocket connections held separately: instance1=${wa} instance2=${wb}`);
  const [ma, mb] = [await metric(A, "chatspace_messages_created_total"), await metric(B, "chatspace_messages_created_total")];
  ok(ma > 0 && mb > 0, `messages created per instance: instance1=${ma} instance2=${mb}`);
  const [pa, pb] = [await metric(A, "chatspace_events_published_total"), await metric(B, "chatspace_events_published_total")];
  ok(pa > 0 && pb > 0, `events published to Redis per instance: instance1=${pa} instance2=${pb}`);
  await a.screenshot({ path: process.env.SHOT_A });
  await b.screenshot({ path: process.env.SHOT_B });
}

if (phase === "after-restart") {
  const b = await mk(), a = await mk(390, 780);
  await step("bob logs in on instance 1 with credentials created on instance 2 (shared PostgreSQL)", async () => {
    await b.goto(A);
    await b.getByLabel("Email").fill("bob@x.io");
    await b.getByLabel("Password").fill("password123");
    await b.getByRole("button", { name: "Log in" }).click();
    await chan(b, "general").click();
    await b.getByRole("heading", { name: /# general/ }).waitFor({ timeout: 15000 });
  });
  await step("history survived the restart", async () => {
    await b.getByText("sent while bob was offline").waitFor();
    await b.getByText("Hello (edited)").first().waitFor();
  });
  await step("FTS still works on persisted data (GIN index/generated column intact)", async () => {
    await b.getByRole("tab", { name: "Search" }).click();
    await b.getByLabel("Search messages").fill("offlin");
    await b.getByRole("button", { name: "Go" }).click();
    await b.locator(".result").first().waitFor({ timeout: 8000 });
  });
  await step("mobile layout renders from the container without horizontal overflow", async () => {
    await a.goto(B);
    await a.getByLabel("Email").fill("alice@x.io");
    await a.getByLabel("Password").fill("password123");
    await a.getByRole("button", { name: "Log in" }).click();
    await a.locator(".chat-head h1").waitFor({ timeout: 15000 });
    ok(await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no overflow");
  });
}
await browser.close();
console.log(failed ? `\n${failed} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failed ? 1 : 0);
