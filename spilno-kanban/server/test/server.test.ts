import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as Y from 'yjs';
import WebSocket from 'ws';

import { client, eventually, server, sleep, synced, tempDb, type Client } from './helpers.js';
import type { RunningServer } from '../src/server.js';

const BOARD = 'board_test_0001';

describe('sync server', () => {
  let db: ReturnType<typeof tempDb>;
  let srv: RunningServer;
  const clients: Client[] = [];
  const open = (board = BOARD) => {
    const c = client(srv, board);
    clients.push(c);
    return c;
  };

  beforeEach(async () => {
    db = tempDb();
    srv = await server(db.path);
  });
  afterEach(async () => {
    for (const c of clients.splice(0)) c.destroy();
    await srv.close().catch(() => {});
    db.cleanup();
  });

  it('syncs edits between two stock y-websocket clients', async () => {
    const a = open();
    const b = open();
    await Promise.all([synced(a), synced(b)]);
    a.doc.getMap('cards').set('c1', 'from A');
    b.doc.getText('t').insert(0, 'hello from B');
    await eventually(() => {
      expect(b.doc.getMap('cards').get('c1')).toBe('from A');
      expect(a.doc.getText('t').toString()).toBe('hello from B');
    });
  });

  it('keeps boards separate', async () => {
    const a = open('board_aaaaaaaaaa');
    const b = open('board_bbbbbbbbbb');
    await Promise.all([synced(a), synced(b)]);
    a.doc.getMap('m').set('k', 1);
    await sleep(100);
    expect(b.doc.getMap('m').get('k')).toBeUndefined();
  });

  it('a late joiner receives the full state', async () => {
    const a = open();
    await synced(a);
    for (let i = 0; i < 50; i++) a.doc.getArray('xs').push([i]);
    await eventually(() => expect(srv.rooms.get(BOARD)?.doc.getArray('xs').length).toBe(50));
    const late = open();
    await synced(late);
    expect(late.doc.getArray('xs').toArray()).toEqual([...Array(50).keys()]);
  });

  it('boards survive a server restart', async () => {
    const a = open();
    await synced(a);
    a.doc.getMap('meta').set('title', 'Roadmap');
    a.doc.getText('desc').insert(0, 'persist me');
    await eventually(() => expect(srv.rooms.get(BOARD)?.doc.getText('desc').toString()).toBe('persist me'));
    a.destroy();
    await srv.close();

    srv = await server(db.path);
    const b = open();
    await synced(b);
    expect(b.doc.getMap('meta').get('title')).toBe('Roadmap');
    expect(b.doc.getText('desc').toString()).toBe('persist me');
  });

  it('compaction folds the log into a snapshot without changing the document', async () => {
    await srv.close();
    srv = await server(db.path, { compactEvery: 20 });
    const a = open();
    await synced(a);
    for (let i = 0; i < 45; i++) a.doc.getText('t').insert(i, 'x');
    await eventually(() => expect(srv.rooms.get(BOARD)?.doc.getText('t').length).toBe(45));
    expect(srv.persistence.pendingUpdates(BOARD)).toBeLessThan(20);
    const before = Y.encodeStateAsUpdate(srv.rooms.get(BOARD)!.doc);

    a.destroy();
    await srv.close();
    srv = await server(db.path);
    const b = open();
    await synced(b);
    expect(b.doc.getText('t').toString()).toBe('x'.repeat(45));
    // The reloaded document contains everything the pre-restart one had: the diff between them is empty.
    const original = new Y.Doc();
    Y.applyUpdate(original, before);
    const missing = Y.encodeStateAsUpdate(original, Y.encodeStateVector(b.doc));
    const probe = new Y.Doc();
    Y.applyUpdate(probe, Y.encodeStateAsUpdate(b.doc));
    Y.applyUpdate(probe, missing);
    expect(probe.getText('t').toString()).toBe(b.doc.getText('t').toString());
    expect(Y.encodeStateVector(probe)).toEqual(Y.encodeStateVector(b.doc));
  });

  it('relays presence and removes it when a client disconnects', async () => {
    const a = open();
    const b = open();
    await Promise.all([synced(a), synced(b)]);
    a.provider.awareness.setLocalStateField('user', { name: 'Оля', color: '#e5484d' });
    await eventually(() => {
      const names = [...b.provider.awareness.getStates().values()].map((s) => s.user?.name);
      expect(names).toContain('Оля');
    });
    const aId = a.doc.clientID;
    a.destroy();
    await eventually(() => expect(b.provider.awareness.getStates().has(aId)).toBe(false));
  });

  it('unloads idle rooms and compacts them', async () => {
    const a = open();
    await synced(a);
    a.doc.getMap('m').set('k', 'v');
    await eventually(() => expect(srv.persistence.pendingUpdates(BOARD)).toBe(1));
    a.destroy();
    await eventually(() => expect(srv.rooms.has(BOARD)).toBe(false));
    expect(srv.persistence.pendingUpdates(BOARD)).toBe(0);
  });

  it('rejects invalid board ids and unknown paths', async () => {
    for (const path of ['/ws/short', '/ws/has%20space_____', '/ws/a/b', '/other/valid_board_id']) {
      const status = await new Promise<number | string>((resolve) => {
        const ws = new WebSocket(`ws://127.0.0.1:${srv.port}${path}`);
        ws.on('unexpected-response', (_req, res) => resolve(res.statusCode ?? 0));
        ws.on('open', () => resolve('opened'));
        ws.on('error', () => {});
      });
      expect(status, path).toBe(400);
    }
  });

  it('closes connections that send oversized, malformed or too many messages', async () => {
    await srv.close();
    srv = await server(db.path, { maxMessageBytes: 1024, messageRate: 1, messageBurst: 5 });
    const raw = () =>
      new Promise<WebSocket>((resolve) => {
        const ws = new WebSocket(`ws://127.0.0.1:${srv.port}/ws/${BOARD}`);
        ws.on('open', () => resolve(ws));
      });
    const closeCode = (ws: WebSocket) => new Promise<number>((resolve) => ws.on('close', (code) => resolve(code)));

    let ws = await raw();
    let closed = closeCode(ws);
    ws.send(new Uint8Array(4096));
    expect(await closed).toBe(1009); // message too big

    ws = await raw();
    closed = closeCode(ws);
    ws.send(new Uint8Array([42, 1, 2]));
    expect(await closed).toBe(1007); // unknown message type

    ws = await raw();
    closed = closeCode(ws);
    for (let i = 0; i < 20; i++) ws.send(new Uint8Array([3])); // query-awareness spam
    expect(await closed).toBe(1008);
  });

  it('limits connections per room', async () => {
    await srv.close();
    srv = await server(db.path, { maxConnectionsPerRoom: 2 });
    await Promise.all([synced(open()), synced(open())]);
    const status = await new Promise<number>((resolve) => {
      const ws = new WebSocket(`ws://127.0.0.1:${srv.port}/ws/${BOARD}`);
      ws.on('unexpected-response', (_req, res) => resolve(res.statusCode ?? 0));
      ws.on('error', () => {});
    });
    expect(status).toBe(503);
  });

  it('stops accepting edits once a board exceeds its size limit', async () => {
    await srv.close();
    srv = await server(db.path, { maxDocBytes: 2000 });
    const a = open();
    await synced(a);
    a.doc.getText('t').insert(0, 'x'.repeat(3000));
    await eventually(() => expect(srv.rooms.get(BOARD)?.readOnly).toBe(true));
    a.doc.getText('t').insert(0, 'more');
    await sleep(150);
    expect(srv.rooms.get(BOARD)!.doc.getText('t').length).toBe(3000);
  });

  it('serves health and metrics', async () => {
    const a = open();
    await synced(a);
    a.doc.getMap('m').set('k', 1);
    await eventually(() => expect(srv.persistence.pendingUpdates(BOARD)).toBe(1));
    expect(await (await fetch(`${srv.url}/healthz`)).json()).toEqual({ status: 'ok' });
    const text = await (await fetch(`${srv.url}/metrics`)).text();
    expect(text).toMatch(/spilno_rooms_loaded 1/);
    expect(text).toMatch(/spilno_clients_in_rooms 1/);
    expect(text).toMatch(/spilno_persisted_updates_total \d+/);
  });
});
