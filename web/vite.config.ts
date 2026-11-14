import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs separately (./bp serve); in development its routes are proxied.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.BLUEPRINTS_API ?? "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1200 },
});
