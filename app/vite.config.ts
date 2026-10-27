import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files land inside the Python package so `reelquick app` serves them with no Node on the user's machine.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../engine/reelquick/ui", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/media": "http://127.0.0.1:8765", "/strip": "http://127.0.0.1:8765", "/clips": "http://127.0.0.1:8765" } },
});
