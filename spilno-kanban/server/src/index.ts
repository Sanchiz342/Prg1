import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';

import { defaults, startServer } from './server.js';

const env = process.env;
const num = (name: string, fallback: number) => {
  const v = Number(env[name]);
  return Number.isFinite(v) && env[name] !== undefined && env[name] !== '' ? v : fallback;
};

const webDist = resolve(fileURLToPath(new URL('.', import.meta.url)), '../../web/dist');

const server = await startServer({
  port: num('PORT', defaults.port),
  host: env.HOST ?? defaults.host,
  dbPath: env.DB_PATH ?? defaults.dbPath,
  staticDir: env.STATIC_DIR ?? (existsSync(webDist) ? webDist : undefined),
  maxMessageBytes: num('MAX_MESSAGE_BYTES', defaults.maxMessageBytes),
  maxDocBytes: num('MAX_DOC_BYTES', defaults.maxDocBytes),
  maxConnectionsPerRoom: num('MAX_CONNECTIONS_PER_ROOM', defaults.maxConnectionsPerRoom),
  messageRate: num('MESSAGE_RATE', defaults.messageRate),
  messageBurst: num('MESSAGE_BURST', defaults.messageBurst),
  compactEvery: num('COMPACT_EVERY', defaults.compactEvery),
  roomIdleMs: num('ROOM_IDLE_MS', defaults.roomIdleMs),
  heartbeatMs: num('HEARTBEAT_MS', defaults.heartbeatMs),
  allowedOrigins: (env.ALLOWED_ORIGINS ?? '').split(',').map((s) => s.trim()).filter(Boolean),
});
console.log(`spilno listening on :${server.port} (db ${env.DB_PATH ?? defaults.dbPath})`);

let stopping = false;
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, async () => {
    if (stopping) return;
    stopping = true;
    console.log(`${signal}: closing connections and compacting boards`);
    await server.close();
    process.exit(0);
  });
}
