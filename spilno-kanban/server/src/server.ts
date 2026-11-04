import { createServer as createHttpServer, type IncomingMessage, type ServerResponse, type Server } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, sep } from 'node:path';
import type { AddressInfo } from 'node:net';
import { WebSocketServer, type WebSocket } from 'ws';

import { Persistence } from './persistence.js';
import { Room } from './room.js';
import { metrics, renderMetrics } from './metrics.js';

export interface ServerOptions {
  port: number;
  host: string;
  dbPath: string;
  /** Directory with the built web app; omitted in tests. */
  staticDir?: string;
  maxMessageBytes: number;
  maxDocBytes: number;
  maxConnectionsPerRoom: number;
  /** Token bucket per connection: sustained messages per second and burst size. */
  messageRate: number;
  messageBurst: number;
  compactEvery: number;
  /** Unload a room this long after its last client leaves. */
  roomIdleMs: number;
  heartbeatMs: number;
  /** Allowed Origin headers for WebSocket upgrades; empty = allow any. */
  allowedOrigins: string[];
}

export const defaults: ServerOptions = {
  port: 8787,
  host: '0.0.0.0',
  dbPath: 'data/spilno.sqlite',
  // A client that edited offline sends its whole state in ONE message on reconnect, so a message must be
  // allowed to be at least as large as a document.
  maxMessageBytes: 10 << 20,
  maxDocBytes: 8 << 20,
  maxConnectionsPerRoom: 50,
  messageRate: 60,
  messageBurst: 300,
  compactEvery: 500,
  roomIdleMs: 30_000,
  heartbeatMs: 30_000,
  allowedOrigins: [],
};

/** Board ids are random, unguessable strings: knowing the id is the permission to edit (like Excalidraw links). */
export const BOARD_ID = /^[A-Za-z0-9_-]{10,64}$/;

export interface RunningServer {
  url: string;
  port: number;
  http: Server;
  rooms: Map<string, Room>;
  persistence: Persistence;
  close(): Promise<void>;
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.woff2': 'font/woff2',
};

export async function startServer(partial: Partial<ServerOptions> = {}): Promise<RunningServer> {
  const opts: ServerOptions = { ...defaults, ...partial };
  if (opts.maxMessageBytes < opts.maxDocBytes) {
    console.warn(
      `MAX_MESSAGE_BYTES (${opts.maxMessageBytes}) < MAX_DOC_BYTES (${opts.maxDocBytes}): large boards edited offline will not be able to sync`,
    );
  }
  const persistence = new Persistence(opts.dbPath);
  const rooms = new Map<string, Room>();
  const idleTimers = new Map<string, NodeJS.Timeout>();

  const getRoom = (id: string): Room => {
    clearTimeout(idleTimers.get(id));
    idleTimers.delete(id);
    let room = rooms.get(id);
    if (!room) {
      room = new Room(id, persistence, { compactEvery: opts.compactEvery, maxDocBytes: opts.maxDocBytes });
      rooms.set(id, room);
    }
    return room;
  };

  const scheduleUnload = (room: Room) => {
    if (room.conns.size > 0) return;
    idleTimers.set(
      room.id,
      setTimeout(() => {
        idleTimers.delete(room.id);
        if (room.conns.size === 0 && rooms.get(room.id) === room) {
          room.destroy();
          rooms.delete(room.id);
        }
      }, opts.roomIdleMs),
    );
  };

  const http = createHttpServer((req, res) => {
    // A single malformed request must never take the process (and every open board) down.
    try {
      handleHttp(req, res, opts, rooms, persistence);
    } catch (err) {
      if (!res.headersSent) res.writeHead(400);
      res.end();
    }
  });
  const wss = new WebSocketServer({ noServer: true, maxPayload: opts.maxMessageBytes });

  http.on('upgrade', (req, socket, head) => {
    let url: URL;
    try {
      url = new URL(req.url ?? '/', 'http://localhost');
    } catch {
      socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    const match = /^\/ws\/([^/]+)$/.exec(url.pathname);
    const reject = (code: number, reason: string) => {
      metrics.rejectedConnections++;
      socket.end(`HTTP/1.1 ${code} ${reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    };
    if (!match || !BOARD_ID.test(match[1]!)) return reject(400, 'Bad Request');
    if (opts.allowedOrigins.length && !opts.allowedOrigins.includes(req.headers.origin ?? '')) {
      return reject(403, 'Forbidden');
    }
    const boardId = match[1]!;
    if ((rooms.get(boardId)?.conns.size ?? 0) >= opts.maxConnectionsPerRoom) return reject(503, 'Room Full');
    wss.handleUpgrade(req, socket, head, (ws) => onConnection(ws, getRoom(boardId)));
  });

  const alive = new WeakMap<WebSocket, boolean>();

  function onConnection(ws: WebSocket, room: Room) {
    metrics.connections++;
    metrics.connectionsTotal++;
    alive.set(ws, true);
    ws.on('pong', () => alive.set(ws, true));
    ws.binaryType = 'nodebuffer';

    let tokens = opts.messageBurst;
    let last = Date.now();
    ws.on('message', (raw: Buffer, isBinary: boolean) => {
      const now = Date.now();
      tokens = Math.min(opts.messageBurst, tokens + ((now - last) / 1000) * opts.messageRate);
      last = now;
      if (tokens < 1) {
        metrics.rateLimited++;
        ws.close(1008, 'rate limit exceeded');
        return;
      }
      tokens -= 1;
      if (!isBinary) {
        ws.close(1003, 'binary messages only');
        return;
      }
      try {
        room.handle(ws, new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength));
      } catch (err) {
        ws.close(1007, 'malformed message');
      }
    });
    ws.on('close', () => {
      metrics.connections--;
      room.leave(ws);
      scheduleUnload(room);
    });
    ws.on('error', () => ws.terminate());
    room.join(ws);
  }

  // Drop connections that stopped answering pings (closed laptops, dead mobile networks).
  const heartbeat = setInterval(() => {
    for (const ws of wss.clients) {
      if (alive.get(ws) === false) {
        ws.terminate();
        continue;
      }
      alive.set(ws, false);
      ws.ping();
    }
  }, opts.heartbeatMs);
  heartbeat.unref();

  await new Promise<void>((done) => http.listen(opts.port, opts.host, done));
  const port = (http.address() as AddressInfo).port;

  return {
    url: `http://127.0.0.1:${port}`,
    port,
    http,
    rooms,
    persistence,
    async close() {
      clearInterval(heartbeat);
      for (const t of idleTimers.values()) clearTimeout(t);
      for (const ws of wss.clients) ws.close(1001, 'server shutting down');
      await new Promise<void>((done) => wss.close(() => done()));
      await new Promise<void>((done) => http.close(() => done()));
      // Everything in memory is already in the log; compaction just tidies it up.
      for (const room of rooms.values()) room.destroy();
      rooms.clear();
      persistence.close();
    },
  };
}

function handleHttp(
  req: IncomingMessage,
  res: ServerResponse,
  opts: ServerOptions,
  rooms: Map<string, Room>,
  persistence: Persistence,
): void {
  const url = new URL(req.url ?? '/', 'http://localhost');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'application/json' }).end('{"status":"ok"}');
    return;
  }
  if (url.pathname === '/metrics') {
    const s = persistence.stats();
    let clientsInRooms = 0;
    for (const r of rooms.values()) clientsInRooms += r.conns.size;
    const body = renderMetrics({
      rooms_loaded: rooms.size,
      clients_in_rooms: clientsInRooms,
      boards_stored: s.boards,
      update_log_rows: s.updates,
      snapshot_bytes: s.snapshot_bytes,
      update_log_bytes: s.update_bytes,
    });
    res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4' }).end(body);
    return;
  }
  if (!opts.staticDir || (req.method !== 'GET' && req.method !== 'HEAD')) {
    res.writeHead(404).end();
    return;
  }
  // Static files with SPA fallback: unknown paths (e.g. /b/<id>) get index.html.
  const root = resolve(opts.staticDir);
  let pathname: string;
  try {
    pathname = decodeURIComponent(url.pathname);
  } catch {
    res.writeHead(400).end(); // malformed percent-encoding, e.g. "/%E0%A4%A"
    return;
  }
  let file = normalize(join(root, pathname));
  if (!file.startsWith(root + sep) && file !== root) {
    res.writeHead(400).end();
    return;
  }
  if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
  if (!existsSync(file)) {
    res.writeHead(404).end();
    return;
  }
  const hashed = file.includes(`${sep}assets${sep}`);
  res.writeHead(200, {
    'Content-Type': MIME[extname(file)] ?? 'application/octet-stream',
    'Cache-Control': hashed ? 'public, max-age=31536000, immutable' : 'no-cache',
  });
  if (req.method === 'HEAD') res.end();
  else createReadStream(file).pipe(res);
}
