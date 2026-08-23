import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is same-origin in the browser, so nothing needs CORS in dev.
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
});
