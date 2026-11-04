import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const PORT = 8790;
const db = join(mkdtempSync(join(tmpdir(), 'spilno-e2e-')), 'e2e.sqlite');

export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  expect: { timeout: 6_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    // Serves the production build (run `npm run build` first; `npm run test:e2e` does it).
    command: 'node server/dist/index.js',
    url: `http://127.0.0.1:${PORT}/healthz`,
    env: { PORT: String(PORT), HOST: '127.0.0.1', DB_PATH: db, ROOM_IDLE_MS: '2000' },
    reuseExistingServer: false,
  },
});
