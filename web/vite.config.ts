import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built to static files that FastAPI serves from src/voxframe/api/static.
// No Next.js, no Node server at runtime: a localhost tool serving one user
// needs neither (D-113).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/voxframe/api/static",
    emptyOutDir: true,
    // Relative paths, so the bundle does not care what port it is served on.
    assetsDir: "assets",
  },
  base: "./",
  server: {
    // Dev only. The real app is served by FastAPI on its own port.
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
