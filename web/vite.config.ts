import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    allowedHosts: ["127.0.0.1"],
  },
  build: {
    outDir: "../src/boss_agent_cli/web/static",
    emptyOutDir: true,
    sourcemap: false,
  },
});
