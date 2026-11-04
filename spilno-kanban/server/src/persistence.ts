// Board storage in SQLite (Node's built-in `node:sqlite`, no native add-ons).
//
// Every Yjs update is appended to `updates` as it arrives (cheap, crash-safe). Periodically and when a room
// unloads, the log is compacted: the whole document is encoded once into `boards.snapshot` and the log rows it
// covers are deleted. Loading = snapshot + remaining log rows; Yjs updates are commutative and idempotent, so
// replaying them in any order yields the same document.
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface StoredBoard {
  snapshot: Uint8Array | null;
  updates: Uint8Array[];
  /** Highest update row id included in `updates` (compaction deletes up to this). */
  lastUpdateId: number;
}

export class Persistence {
  private db: DatabaseSync;
  private stmt: {
    load: ReturnType<DatabaseSync['prepare']>;
    loadUpdates: ReturnType<DatabaseSync['prepare']>;
    append: ReturnType<DatabaseSync['prepare']>;
    upsertSnapshot: ReturnType<DatabaseSync['prepare']>;
    deleteUpdates: ReturnType<DatabaseSync['prepare']>;
    countUpdates: ReturnType<DatabaseSync['prepare']>;
    stats: ReturnType<DatabaseSync['prepare']>;
  };

  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = NORMAL;
      CREATE TABLE IF NOT EXISTS boards (
        id TEXT PRIMARY KEY,
        snapshot BLOB,
        updated_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        board_id TEXT NOT NULL,
        data BLOB NOT NULL,
        created_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS updates_board ON updates (board_id, id);
    `);
    this.stmt = {
      load: this.db.prepare('SELECT snapshot FROM boards WHERE id = ?'),
      loadUpdates: this.db.prepare('SELECT id, data FROM updates WHERE board_id = ? ORDER BY id'),
      append: this.db.prepare('INSERT INTO updates (board_id, data, created_at) VALUES (?, ?, ?)'),
      upsertSnapshot: this.db.prepare(
        `INSERT INTO boards (id, snapshot, updated_at) VALUES (?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot, updated_at = excluded.updated_at`,
      ),
      deleteUpdates: this.db.prepare('DELETE FROM updates WHERE board_id = ? AND id <= ?'),
      countUpdates: this.db.prepare('SELECT COUNT(*) AS n FROM updates WHERE board_id = ?'),
      stats: this.db.prepare(
        `SELECT (SELECT COUNT(*) FROM boards) AS boards, (SELECT COUNT(*) FROM updates) AS updates,
                (SELECT COALESCE(SUM(LENGTH(snapshot)), 0) FROM boards) AS snapshot_bytes,
                (SELECT COALESCE(SUM(LENGTH(data)), 0) FROM updates) AS update_bytes`,
      ),
    };
  }

  load(boardId: string): StoredBoard {
    const row = this.stmt.load.get(boardId) as { snapshot: Uint8Array | null } | undefined;
    const rows = this.stmt.loadUpdates.all(boardId) as { id: number; data: Uint8Array }[];
    return {
      snapshot: row?.snapshot ? new Uint8Array(row.snapshot) : null,
      updates: rows.map((r) => new Uint8Array(r.data)),
      lastUpdateId: rows.length ? rows[rows.length - 1]!.id : 0,
    };
  }

  /** Appends one update; returns its row id. */
  append(boardId: string, update: Uint8Array): number {
    const res = this.stmt.append.run(boardId, update, Date.now());
    return Number(res.lastInsertRowid);
  }

  /** Atomically stores `snapshot` and drops log rows up to `upToId` (which the snapshot includes). */
  compact(boardId: string, snapshot: Uint8Array, upToId: number): void {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.stmt.upsertSnapshot.run(boardId, snapshot, Date.now());
      this.stmt.deleteUpdates.run(boardId, upToId);
      this.db.exec('COMMIT');
    } catch (err) {
      this.db.exec('ROLLBACK');
      throw err;
    }
  }

  pendingUpdates(boardId: string): number {
    return Number((this.stmt.countUpdates.get(boardId) as { n: number }).n);
  }

  stats(): { boards: number; updates: number; snapshot_bytes: number; update_bytes: number } {
    return this.stmt.stats.get() as { boards: number; updates: number; snapshot_bytes: number; update_bytes: number };
  }

  close(): void {
    this.db.close();
  }
}
