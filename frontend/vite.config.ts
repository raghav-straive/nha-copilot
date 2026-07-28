import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The base path decides the URL prefix under which the built app is served.
//
//   - GitHub Pages serves from https://<user>.github.io/nha-copilot/, so the
//     default in a production build stays "/nha-copilot/".
//   - Self-hosting at a domain ROOT (e.g. https://copilot.nha.internal/)?
//     Set VITE_BASE=/ in frontend/.env  (see frontend/.env.example).
//   - Self-hosting under a SUBPATH (e.g. https://portal.nha.internal/copilot/)?
//     Set VITE_BASE=/copilot/  (keep both leading and trailing slashes).
//
// VITE_BASE (and VITE_API_BASE) are read from frontend/.env, from a real
// environment variable, or from the CI `env:` block — whichever is present.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const base = env.VITE_BASE || (mode === "production" ? "/nha-copilot/" : "/");

  return {
    base,
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // Dev only: proxy API calls to the local FastAPI backend.
        "/auth": "http://localhost:8000",
        "/chat": "http://localhost:8000",
        "/query-log": "http://localhost:8000",
        "/report": "http://localhost:8000",
        "/explorer": "http://localhost:8000",
        "/pdfchat": "http://localhost:8000",
        "/health": "http://localhost:8000",
      },
    },
  };
});
