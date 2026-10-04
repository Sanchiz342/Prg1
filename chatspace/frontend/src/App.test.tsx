import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { AuthProvider } from "./auth";

class QuietSocket {
  static OPEN = 1;
  readyState = 1;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose = null; onerror = null;
  constructor() { setTimeout(() => this.onmessage?.({ data: JSON.stringify({ type: "ready", user_id: "u1" }) }), 0); }
  send() {} close() {}
}

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AuthProvider><App /></AuthProvider></QueryClientProvider>);
}

beforeEach(() => { localStorage.clear(); vi.stubGlobal("WebSocket", QuietSocket); });
afterEach(() => vi.unstubAllGlobals());

test("login failure shows the server error", async () => {
  vi.stubGlobal("fetch", vi.fn(() => json({ detail: "Invalid credentials" }, 401)));
  mount();
  await userEvent.type(screen.getByLabelText("Email"), "a@x.io");
  await userEvent.type(screen.getByLabelText("Password"), "password123");
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials");
});

test("full flow: login → channel → history → optimistic send", async () => {
  const user = { id: "u1", username: "alice", email: "a@x.io" };
  const sent: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const m = init?.method ?? "GET";
    if (url === "/api/auth/login") return json({ access_token: "tok", user });
    if (url === "/api/workspaces") return json([{ id: "w1", name: "PRG1", role: "OWNER" }]);
    if (url === "/api/workspaces/w1/channels") return json([{ id: "c1", workspace_id: "w1", name: "general", type: "PUBLIC" }]);
    if (url === "/api/workspaces/w1/members") return json([{ id: "u1", username: "alice", role: "OWNER", presence: "ONLINE" }]);
    if (url === "/api/notifications") return json([]);
    if (url.startsWith("/api/channels/c1/messages") && m === "GET")
      return json({ has_more: false, next_before: null, messages: [{ id: "m1", channel_id: "c1", author_id: "u1", content: "hello team", reply_to: null, created_at: "2025-01-01T10:00:00Z", updated_at: null, deleted_at: null, reactions: { "👍": 2 }, mine: [], reply_count: 0 }] });
    if (url === "/api/channels/c1/messages" && m === "POST") {
      sent.push(JSON.parse(init!.body as string));
      return json({ id: "m2", channel_id: "c1", author_id: "u1", content: "second", reply_to: null, created_at: "2025-01-01T10:01:00Z", updated_at: null, deleted_at: null, reactions: {}, mine: [], reply_count: 0 }, 201);
    }
    return json({ detail: "unexpected " + m + " " + url }, 500);
  }));

  mount();
  await userEvent.type(screen.getByLabelText("Email"), "a@x.io");
  await userEvent.type(screen.getByLabelText("Password"), "password123");
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));

  expect(await screen.findByText("hello team")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /general/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /👍 2/ })).toBeInTheDocument();

  await userEvent.type(screen.getByLabelText("Message #general"), "second{Enter}");
  expect(await screen.findByText("second")).toBeInTheDocument();
  await waitFor(() => expect(sent).toEqual([{ content: "second", reply_to: null }]));
  await waitFor(() => expect(document.querySelector(".msg.pending")).toBeNull());
});
