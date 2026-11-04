// One room = one board document shared by every connected client.
//
// Wire format is the y-websocket protocol, so the stock `y-websocket` client works unchanged:
//   [varUint 0 = sync][y-protocols/sync message]       document state exchange and incremental updates
//   [varUint 1 = awareness][awareness update]         presence (names, colours, who edits what), never stored
//   [varUint 3 = query awareness]                       client asks for everyone's presence
import * as Y from 'yjs';
import * as syncProtocol from 'y-protocols/sync';
import * as awarenessProtocol from 'y-protocols/awareness';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';
import type { WebSocket } from 'ws';

import type { Persistence } from './persistence.js';
import { metrics } from './metrics.js';

export const MESSAGE_SYNC = 0;
export const MESSAGE_AWARENESS = 1;
export const MESSAGE_QUERY_AWARENESS = 3;

/** Origin for updates loaded from disk, so they are not written back. */
const PERSISTENCE_ORIGIN = Symbol('persistence');

export interface RoomOptions {
  /** Compact the update log once it holds this many rows. */
  compactEvery: number;
  /** Reject further edits once the encoded document exceeds this many bytes. */
  maxDocBytes: number;
}

export class Room {
  readonly doc = new Y.Doc({ gc: true });
  readonly awareness = new awarenessProtocol.Awareness(this.doc);
  /** Connection -> awareness client ids it controls (removed when it disconnects). */
  readonly conns = new Map<WebSocket, Set<number>>();
  private lastUpdateId = 0;
  private pendingRows = 0;
  private docBytes = 0;
  readOnly = false;

  constructor(
    readonly id: string,
    private persistence: Persistence,
    private opts: RoomOptions,
  ) {
    // Nobody is "the server" in presence: no local state.
    this.awareness.setLocalState(null);

    const stored = persistence.load(id);
    Y.transact(
      this.doc,
      () => {
        if (stored.snapshot) Y.applyUpdate(this.doc, stored.snapshot);
        for (const u of stored.updates) Y.applyUpdate(this.doc, u);
      },
      PERSISTENCE_ORIGIN,
    );
    this.lastUpdateId = stored.lastUpdateId;
    this.pendingRows = stored.updates.length;
    this.docBytes = Y.encodeStateAsUpdate(this.doc).byteLength;
    this.readOnly = this.docBytes > opts.maxDocBytes;

    this.doc.on('update', (update: Uint8Array, origin: unknown) => {
      if (origin !== PERSISTENCE_ORIGIN) {
        this.lastUpdateId = persistence.append(id, update);
        this.pendingRows++;
        metrics.persistedUpdates++;
        metrics.persistedBytes += update.byteLength;
        this.docBytes += update.byteLength; // upper bound; exact size is recomputed on compaction
        if (this.pendingRows >= opts.compactEvery) this.compact();
        if (this.docBytes > opts.maxDocBytes) this.readOnly = this.encodedSize() > opts.maxDocBytes;
      }
      const msg = encoding.createEncoder();
      encoding.writeVarUint(msg, MESSAGE_SYNC);
      syncProtocol.writeUpdate(msg, update);
      this.broadcast(encoding.toUint8Array(msg));
    });

    this.awareness.on(
      'update',
      ({ added, updated, removed }: { added: number[]; updated: number[]; removed: number[] }, origin: unknown) => {
        const changed = added.concat(updated, removed);
        const owner = origin as WebSocket | null;
        if (owner && this.conns.has(owner)) {
          const ids = this.conns.get(owner)!;
          for (const c of added) ids.add(c);
          for (const c of removed) ids.delete(c);
        }
        const msg = encoding.createEncoder();
        encoding.writeVarUint(msg, MESSAGE_AWARENESS);
        encoding.writeVarUint8Array(msg, awarenessProtocol.encodeAwarenessUpdate(this.awareness, changed));
        this.broadcast(encoding.toUint8Array(msg));
      },
    );
  }

  private encodedSize(): number {
    this.docBytes = Y.encodeStateAsUpdate(this.doc).byteLength;
    return this.docBytes;
  }

  /** Folds the update log into one snapshot row. */
  compact(): void {
    if (this.pendingRows === 0) return;
    const snapshot = Y.encodeStateAsUpdate(this.doc);
    this.persistence.compact(this.id, snapshot, this.lastUpdateId);
    this.pendingRows = 0;
    this.docBytes = snapshot.byteLength;
    metrics.compactions++;
  }

  join(conn: WebSocket): void {
    this.conns.set(conn, new Set());
    // Start the handshake: our state vector, so the client sends what we lack...
    const sync = encoding.createEncoder();
    encoding.writeVarUint(sync, MESSAGE_SYNC);
    syncProtocol.writeSyncStep1(sync, this.doc);
    this.send(conn, encoding.toUint8Array(sync));
    // ...and everyone's current presence.
    const states = this.awareness.getStates();
    if (states.size > 0) {
      const aw = encoding.createEncoder();
      encoding.writeVarUint(aw, MESSAGE_AWARENESS);
      encoding.writeVarUint8Array(aw, awarenessProtocol.encodeAwarenessUpdate(this.awareness, [...states.keys()]));
      this.send(conn, encoding.toUint8Array(aw));
    }
  }

  /** Handles one binary message from `conn`. Throws on malformed input (caller closes the connection). */
  handle(conn: WebSocket, data: Uint8Array): void {
    const decoder = decoding.createDecoder(data);
    const type = decoding.readVarUint(decoder);
    switch (type) {
      case MESSAGE_SYNC: {
        metrics.messages.sync++;
        const reply = encoding.createEncoder();
        encoding.writeVarUint(reply, MESSAGE_SYNC);
        // Step 2 and updates mutate the doc; refuse them for an oversized (read-only) board.
        const peek = decoding.createDecoder(data);
        decoding.readVarUint(peek);
        const syncType = decoding.readVarUint(peek);
        if (this.readOnly && syncType !== syncProtocol.messageYjsSyncStep1) {
          metrics.rejectedUpdates++;
          return;
        }
        syncProtocol.readSyncMessage(decoder, reply, this.doc, conn);
        if (encoding.length(reply) > 1) this.send(conn, encoding.toUint8Array(reply));
        break;
      }
      case MESSAGE_AWARENESS:
        metrics.messages.awareness++;
        awarenessProtocol.applyAwarenessUpdate(this.awareness, decoding.readVarUint8Array(decoder), conn);
        break;
      case MESSAGE_QUERY_AWARENESS: {
        const aw = encoding.createEncoder();
        encoding.writeVarUint(aw, MESSAGE_AWARENESS);
        encoding.writeVarUint8Array(
          aw,
          awarenessProtocol.encodeAwarenessUpdate(this.awareness, [...this.awareness.getStates().keys()]),
        );
        this.send(conn, encoding.toUint8Array(aw));
        break;
      }
      default:
        throw new Error(`unknown message type ${type}`);
    }
  }

  leave(conn: WebSocket): void {
    const ids = this.conns.get(conn);
    this.conns.delete(conn);
    if (ids && ids.size > 0) awarenessProtocol.removeAwarenessStates(this.awareness, [...ids], null);
  }

  private broadcast(msg: Uint8Array): void {
    for (const conn of this.conns.keys()) this.send(conn, msg);
  }

  private send(conn: WebSocket, msg: Uint8Array): void {
    if (conn.readyState !== conn.OPEN) return;
    conn.send(msg, (err) => {
      if (err) conn.terminate();
    });
  }

  destroy(): void {
    this.compact();
    this.awareness.destroy();
    this.doc.destroy();
  }
}
