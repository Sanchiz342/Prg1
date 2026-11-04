# Architecture

## Document model (`web/src/board/model.ts`)
```
Y.Doc
├── meta     Y.Map   { title }
├── columns  Y.Map   columnId -> Y.Map { title, key }
└── cards    Y.Map   cardId   -> Y.Map { title, description: Y.Text, pos: {col, key}, labels[], assignee, createdAt }
```
- **Maps instead of arrays.** Both collections are maps keyed by random ids. Order comes from fractional `key`s
  (`order.ts`), not from list positions.
- **`pos` is one JSON value.** Column and key are written together, so concurrent moves can never mix the column
  from one move with the key from another.
- **All local edits use the `LOCAL` origin.** The undo manager tracks only that origin, which is why Ctrl+Z
  never reverts remote changes.
- **Initialising a board is idempotent.** It runs only for a brand-new random id and uses fixed ids for the
  default columns, so two clients initialising at once write the same keys.

### Fractional keys (`order.ts`)
- **Format.** Base-62 fractions, ASCII-ordered, with no trailing zeros.
- **`keyBetween(a, b)`:**
  - with both neighbours, it takes a midpoint;
  - with an open end, it bumps or decreases the first digit, so keys grow by about one character per 61
    appends;
  - it then adds a 2-digit random suffix when that keeps the key in range.
- **`keyAt`.** Handles neighbours that share a key by skipping to the next strictly greater key.

## Sync protocol (server `room.ts`)
These are y-websocket messages (`varUint` type + payload):

| type | meaning | stored? |
|---|---|---|
| 0 sync | step 1: state vector; step 2: missing updates; then incremental updates | yes, appended to the log |
| 1 awareness | presence: name, colour, `editing`, `dragging` | no, kept in memory and removed on disconnect |
| 3 query awareness | ask for everyone's presence | — |

On connect, the server sends its state vector (sync step 1) and the current awareness states. Every doc update
is persisted, then broadcast to all connections in the room.

## Persistence (`persistence.ts`)
```
boards  (id, snapshot BLOB, updated_at)
updates (id, board_id, data BLOB, created_at)      -- append-only log
```
- **Load.** A board loads as its snapshot plus the log rows after it. Yjs updates are commutative and
  idempotent, so the replay order does not matter.
- **Compaction.** `BEGIN IMMEDIATE` → upsert the snapshot (the full encoded state) → delete log rows up to the
  last id the snapshot includes → `COMMIT`. Node runs this synchronously, so no update can slip in between
  encoding and deleting.
- **When it runs.** Every `COMPACT_EVERY` updates (500), when a room goes idle, and on shutdown.
- **SQLite settings.** WAL mode with `synchronous=NORMAL`.

## Limits (env vars)
| variable | default | |
|---|---|---|
| `MAX_MESSAGE_BYTES` | 1 MiB | larger frames close the socket (1009) |
| `MESSAGE_RATE` / `MESSAGE_BURST` | 60/s, 300 | token bucket per connection (1008 when exceeded) |
| `MAX_CONNECTIONS_PER_ROOM` | 50 | further upgrades get HTTP 503 |
| `MAX_DOC_BYTES` | 10 MiB | above this the board becomes read-only (edits are dropped) |
| `ROOM_IDLE_MS` | 30 s | unload and compact a room after its last client leaves |
| `HEARTBEAT_MS` | 30 s | ping; sockets that do not answer are terminated |
| `ALLOWED_ORIGINS` | (any) | comma-separated `Origin` allow-list for upgrades |

## Client session (`web/src/session.ts`)
`BoardSession` owns the Y.Doc, the IndexedDB persistence, the WebSocket provider, presence and the undo manager.
- **Rendering.** React subscribes through `useSyncExternalStore`. Bursts of updates (typing, a peer dragging)
  are coalesced into one snapshot per animation frame.
- **Connection state.** It is derived from the socket's real state plus `navigator.onLine`.

## Text binding (`textBinding.ts`, `CardDrawer.tsx`)
- **Local typing.** A keystroke becomes one minimal edit, `diff(old, new, caret)`, applied in a `LOCAL`
  transaction.
- **Remote edits.** They are written straight into the uncontrolled `<textarea>` inside the Yjs observer, so the
  DOM never lags behind the shared text. The caret is moved with `transformPosition`, which the property test
  checks against Yjs relative positions.
