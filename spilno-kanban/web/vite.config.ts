import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In development the sync server runs separately; proxy WebSockets to it.
    proxy: { '/ws': { target: 'ws://127.0.0.1:8787', ws: true } },
  },
  build: { sourcemap: true },
  test: { include: ['test/**/*.test.ts'], environment: 'node' },
});
