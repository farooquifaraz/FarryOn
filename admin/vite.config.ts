import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend so no CORS setup is needed
// locally. In production the built SPA is served as a static site and
// VITE_API_BASE_URL points at the real backend (see src/lib/api.ts).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // The panel is one shared module graph: api.ts holds a module-level
    // impersonation token and a single-flight refresh promise. Left to leak
    // between files, one test's impersonation would silently change another's
    // Authorization header. A fresh module registry per file is cheap here and
    // removes a whole class of order-dependent failure.
    isolate: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
