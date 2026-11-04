// Measures sync latency and storage with real y-websocket clients and the real board model.
//   npx tsx server/bench/bench.ts
// Everything (server and clients) runs in this one process on this machine: the numbers include
// encoding, the WebSocket stack and the server, but no real network.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir, cpus } from 'node:os';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import WebSocket from 'ws';

import { startServer } from '../src/server.js';
import { Board, LOCAL } from '../../web/src/board/model.js';

process.setMaxListeners(200); // 50 y-websocket providers in one process each add an exit hook
const dir = mkdtempSync(join(tmpdir(), 'spilno-bench-'));
const srv = await startServer({ port: 0, host: '127.0.0.1', dbPath: join(dir, 'b.sqlite'), compactEvery: 1_000_000, messageRate: 1e6, messageBurst: 1e6, maxConnectionsPerRoom: 100 });

function client(board: string) {
  const doc = new Y.Doc();
  const provider = new WebsocketProvider(`ws://127.0.0.1:${srv.port}/ws`, board, doc, {
    WebSocketPolyfill: WebSocket as unknown as typeof globalThis.WebSocket,
    disableBc: true,
  });
  const synced = new Promise<void>((r) => provider.once('sync', () => r()));
  return { doc, provider, synced };
}

const pct = (xs: number[], p: number) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(p * xs.length))]!;
const fmt = (ms: number) => `${ms.toFixed(2)} ms`;

// 1. Two people: time from a local edit to the other client observing it.
{
  const a = client('bench_two_people'), b = client('bench_two_people');
  await Promise.all([a.synced, b.synced]);
  const board = new Board(a.doc);
  board.init('Bench');
  const col = board.view().columns[0]!.id;
  const samples: number[] = [];
  for (let i = 0; i < 500; i++) {
    const seen = new Promise<void>((resolve) => b.doc.once('update', () => resolve()));
    const t0 = performance.now();
    if (i % 3 === 0) board.addCard(col, `card ${i}`);
    else if (i % 3 === 1) board.moveCard(board.view().columns[0]!.cards[0]!.id, col, 0);
    else a.doc.transact(() => board.view().columns[0]!.cards[0]!.description!.insert(0, 'x'), LOCAL);
    await seen;
    samples.push(performance.now() - t0);
  }
  console.log(`edit → other client (500 edits): p50 ${fmt(pct(samples, 0.5))}, p95 ${fmt(pct(samples, 0.95))}, max ${fmt(Math.max(...samples))}`);
  a.provider.destroy();
  b.provider.destroy();
}

// 2. Fan-out: one writer, 49 listeners in the same room; time until the LAST listener has the edit.
{
  const clients = Array.from({ length: 50 }, () => client('bench_fanout_room'));
  await Promise.all(clients.map((c) => c.synced));
  const [writer, ...listeners] = clients;
  const samples: number[] = [];
  for (let i = 0; i < 100; i++) {
    const all = Promise.all(listeners.map((l) => new Promise<void>((r) => l.doc.once('update', () => r()))));
    const t0 = performance.now();
    writer!.doc.getMap('m').set('k', i);
    await all;
    samples.push(performance.now() - t0);
  }
  console.log(`edit → all 49 other clients (100 edits): p50 ${fmt(pct(samples, 0.5))}, p95 ${fmt(pct(samples, 0.95))}`);
  for (const c of clients) c.provider.destroy();
}

// 3. Storage: a realistic session of 2,000 edits, before and after compaction.
{
  const a = client('bench_storage_room');
  await a.synced;
  const board = new Board(a.doc);
  board.init('Storage');
  const cols = board.view().columns.map((c) => c.id);
  for (let i = 0; i < 2000; i++) {
    const v = board.view();
    const cards = v.columns.flatMap((c) => c.cards);
    const r = i % 10;
    if (r < 3 || cards.length < 5) board.addCard(cols[i % 3]!, `Задача ${i}`);
    else if (r < 6) board.moveCard(cards[i % cards.length]!.id, cols[(i * 7) % 3]!, i % 4);
    else if (r < 9) a.doc.transact(() => cards[i % cards.length]!.description!.insert(0, 'нотатка '), LOCAL);
    else board.deleteCard(cards[i % cards.length]!.id);
  }
  const room = 'bench_storage_room';
  await new Promise((r) => setTimeout(r, 300));
  const before = srv.persistence.stats();
  srv.rooms.get(room)!.compact();
  const after = srv.persistence.stats();
  const cards = board.view().columns.flatMap((c) => c.cards).length;
  console.log(
    `2,000 edits (${cards} cards remain): update log ${before.update_bytes.toLocaleString('en')} B in ${before.updates.toLocaleString('en')} rows → ` +
      `snapshot ${after.snapshot_bytes.toLocaleString('en')} B after compaction`,
  );
  a.provider.destroy();
}

console.log(`(${cpus().length} CPUs, ${cpus()[0]?.model ?? 'unknown CPU'}, Node ${process.version})`);
await srv.close();
rmSync(dir, { recursive: true, force: true });
process.exit(0);
