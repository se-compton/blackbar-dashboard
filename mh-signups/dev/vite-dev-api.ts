import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin, ViteDevServer } from "vite";

/**
 * Local development only (apply: "serve"). Mounts the same handlers the Netlify Functions use,
 * so `npm run dev` works without the Netlify CLI. Never part of the production build.
 * Uses an in-memory store, loads .env without variable expansion (bcrypt hashes contain "$"),
 * and, in live mode, runs the 30-minute sync on a local timer.
 */

function loadDotEnv(root: string): void {
  for (const name of [".env.local", ".env"]) {
    const path = `${root}/${name}`;
    if (!existsSync(path)) continue;
    for (const [k, v] of Object.entries(parseEnv(readFileSync(path, "utf8")))) {
      if (process.env[k] === undefined) process.env[k] = v;
    }
  }
}

async function readBody(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

async function toRequest(req: IncomingMessage): Promise<Request> {
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (typeof v === "string") headers.set(k, v);
    else if (Array.isArray(v)) headers.set(k, v.join(", "));
  }
  const method = req.method ?? "GET";
  const body = method === "GET" || method === "HEAD" ? undefined : await readBody(req);
  return new Request(`http://${req.headers.host ?? "localhost"}${req.url ?? "/"}`, { method, headers, body: body as BodyInit | undefined });
}

async function send(res: ServerResponse, response: Response): Promise<void> {
  res.statusCode = response.status;
  response.headers.forEach((value, key) => {
    if (key.toLowerCase() !== "set-cookie") res.setHeader(key, value);
  });
  const cookies = response.headers.getSetCookie();
  if (cookies.length > 0) res.setHeader("set-cookie", cookies);
  res.end(Buffer.from(await response.arrayBuffer()));
}

export function devApi(): Plugin {
  return {
    name: "mh-signups-dev-api",
    apply: "serve",
    configureServer(server: ViteDevServer) {
      loadDotEnv(server.config.root);

      server.middlewares.use(async (req, res, next) => {
        const path = (req.url ?? "").split("?")[0];
        const route = path === "/api/stats" ? "stats" : path === "/api/login" ? "login" : path === "/api/logout" ? "logout" : null;
        if (route === null) return next();
        try {
          const handlers = await server.ssrLoadModule("/server/handlers.ts");
          const runtime = await server.ssrLoadModule("/server/runtime.ts");
          const info = { ip: req.socket.remoteAddress, deployContext: "dev" };
          const rt = runtime.systemRuntime();
          const request = await toRequest(req);
          const fn = route === "stats" ? handlers.handleStats : route === "login" ? handlers.handleLogin : handlers.handleLogout;
          await send(res, await fn(request, info, rt));
        } catch (err) {
          console.error("dev api error:", err instanceof Error ? err.name : "unknown");
          res.statusCode = 500;
          res.setHeader("Cache-Control", "private, no-store");
          res.end(JSON.stringify({ error: "server_error" }));
        }
      });

      // Live mode locally: no Netlify scheduler, so run the sync here at start and every 30 minutes.
      const runSync = async () => {
        if ((process.env.DATA_MODE ?? "").toLowerCase() !== "live") return;
        const handlers = await server.ssrLoadModule("/server/handlers.ts");
        const runtime = await server.ssrLoadModule("/server/runtime.ts");
        await handlers.handleScheduledRefresh({ deployContext: "dev" }, runtime.systemRuntime());
      };
      server.httpServer?.once("listening", () => {
        void runSync().catch(() => {});
        setInterval(() => void runSync().catch(() => {}), 30 * 60_000).unref();
      });
    },
  };
}
