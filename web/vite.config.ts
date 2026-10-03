import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const vendor = (name: string) => fileURLToPath(new URL(`./src/vendor/${name}`, import.meta.url));

// Built to static files that FastAPI serves from src/voxframe/api/static.
// No Next.js, no Node server at runtime: a localhost tool serving one user
// needs neither (D-113).
export default defineConfig({
  plugins: [react()],
  resolve: {
    // Two of the caption renderer's dependencies are left out (D-196): a
    // GPL-3.0 polyfill no supported browser needs, and an online font lookup
    // Voxframe never uses. See src/vendor.
    alias: {
      "rvfc-polyfill": vendor("no-rvfc-polyfill.ts"),
      "lfa-ponyfill": vendor("no-remote-fonts.ts"),
    },
  },
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
