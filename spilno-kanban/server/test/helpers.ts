import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import WebSocket from 'ws';

import { startServer, type RunningServer, type ServerOptions } from '../src/server.js';

export function tempDb(): { path: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), 'spilno-test-'));
  return { path: join(dir, 'db.sqlite'), cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

export async function server(dbPath: string, opts: Partial<ServerOptions> = {}): Promise<RunningServer> {
  return startServer({ port: 0, host: '127.0.0.1', dbPath, roomIdleMs: 50, heartbeatMs: 60_000, ...opts });
}

export interface Client {
  doc: Y.Doc;
  provider: WebsocketProvider;
  destroy(): void;
}

/** A real y-websocket client (the same library the browser app uses). */
export function client(srv: RunningServer, board: string): Client {
  const doc = new Y.Doc();
  const provider = new WebsocketProvider(`ws://127.0.0.1:${srv.port}/ws`, board, doc, {
    WebSocketPolyfill: WebSocket as unknown as typeof globalThis.WebSocket,
    disableBc: true,
    resyncInterval: -1,
  });
  return {
    doc,
    provider,
    destroy() {
      provider.destroy();
      doc.destroy();
    },
  };
}

export function synced(c: Client): Promise<void> {
  if (c.provider.synced) return Promise.resolve();
  return new Promise((resolve) => c.provider.once('sync', (s: boolean) => s && resolve()));
}

/** Polls until `check` passes (or fails the test after `ms`). */
export async function eventually(check: () => void, ms = 3000): Promise<void> {
  const start = Date.now();
  for (;;) {
    try {
      check();
      return;
    } catch (err) {
      if (Date.now() - start > ms) throw err;
      await new Promise((r) => setTimeout(r, 10));
    }
  }
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
