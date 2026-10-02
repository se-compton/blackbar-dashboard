import { defineConfig } from "vite";
import { devApi } from "./dev/vite-dev-api.ts";

// Only variables prefixed VITE_ are ever exposed to the browser. This app defines none, and
// no secret may ever be given that prefix.
export default defineConfig({
  plugins: [devApi()],
  build: { target: "es2022", sourcemap: false },
  server: { host: true },
});
