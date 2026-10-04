import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev, the API (and WebSocket) are proxied to the FastAPI backend.
const backend = process.env.DEPLOYBOARD_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": backend, "/ws": { target: backend, ws: true } } },
  test: { environment: "node" },
});
