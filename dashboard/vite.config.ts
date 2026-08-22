import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // The API serves this build under /dashboard, so asset URLs must be
  // prefixed or index.html references /assets/* and 404s.
  base: "/dashboard/",
  server: {
    port: 5173,
    proxy: {
      "/viz": "http://127.0.0.1:8000",
      "/api": "http://127.0.0.1:8000",
    },
  },
});
