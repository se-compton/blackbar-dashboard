import {
  clearFailedLogins,
  createSessionToken,
  hasValidSession,
  recordFailedLogin,
  throttleRetryAfter,
  verifyPassphrase,
} from "./auth.ts";
import { authRequired, loadAuthConfig, loadDataConfig, snapshotStoreName } from "./config.ts";
import { clearedSessionCookie, isSameOrigin, jsonResponse, methodNotAllowed, sessionCookie } from "./http.ts";
import { runRefresh } from "./refresh.ts";
import type { RequestInfo, Runtime } from "./runtime.ts";
import { selectSource } from "./source.ts";
import { SnapshotStore } from "./snapshot-store.ts";
import { buildStats } from "./stats-service.ts";
import { parseStatsResponse } from "../shared/stats.ts";

const AUTH_STORE = "mh-signups-auth";
const DEMO_REFRESH_AFTER_MS = 5 * 60_000;

function configError(rt: Runtime, event: string, problems: string[]): Response {
  rt.log.error(event, { problems: problems.join("; ") });
  return jsonResponse({ error: "not_configured" }, 503);
}

/** GET /api/stats. Also reachable through any direct function route: the checks live here, not in routing. */
export async function handleStats(req: Request, info: RequestInfo, rt: Runtime): Promise<Response> {
  if (req.method !== "GET") return methodNotAllowed("GET");

  const data = loadDataConfig(rt.env);
  if (!data.ok) return configError(rt, "data_config_invalid", data.problems);
  const auth = loadAuthConfig(rt.env);
  if (auth.kind === "invalid") return configError(rt, "auth_config_invalid", auth.problems);

  if (authRequired(data.config.dataMode, auth)) {
    // Fail closed: live mode with no (or incomplete) authentication settings serves nothing.
    if (auth.kind !== "enabled") return jsonResponse({ error: "auth_not_configured" }, 503);
    if (!(await hasValidSession(req, auth, rt.now()))) return jsonResponse({ error: "unauthorized" }, 401);
  }

  const now = rt.now();
  const snapshots = new SnapshotStore(rt.openKv(snapshotStoreName(data.config.dataMode, info.deployContext)));
  const source = selectSource(data.config, rt.env, () => now);

  // Demo only: keep the synthetic snapshot fresh on demand, since nothing schedules it locally.
  // Live mode never syncs from a page request; only the scheduled function talks to Lead Docket.
  if (data.config.dataMode === "demo") {
    const existing = await snapshots.readSnapshot();
    if (!existing || now.getTime() - Date.parse(existing.asOf) > DEMO_REFRESH_AFTER_MS) {
      await runRefresh({ config: data.config, source, snapshots, now: rt.now, sleep: rt.sleep, log: rt.log, minAttemptSpacingMs: 0 });
    }
  }

  const stats = await buildStats({ config: data.config, snapshots, now, sourceConfigured: source.isConfigured() });
  return jsonResponse(parseStatsResponse(stats));
}

async function readJson(req: Request): Promise<unknown | null> {
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) return null;
  const text = await req.text();
  if (text.length > 1024) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** POST /api/login with {"passphrase": "..."}. */
export async function handleLogin(req: Request, info: RequestInfo, rt: Runtime): Promise<Response> {
  if (req.method !== "POST") return methodNotAllowed("POST");
  if (!isSameOrigin(req)) return jsonResponse({ error: "forbidden" }, 403);

  const auth = loadAuthConfig(rt.env);
  if (auth.kind === "invalid") return configError(rt, "auth_config_invalid", auth.problems);
  if (auth.kind !== "enabled") return jsonResponse({ error: "auth_not_configured" }, 503);

  const kv = rt.openKv(AUTH_STORE);
  const now = rt.now();
  const wait = await throttleRetryAfter(kv, info.ip, auth.sessionSecret, now);
  if (wait > 0) return jsonResponse({ error: "too_many_attempts", retryAfterSeconds: wait }, 429, { "Retry-After": String(wait) });

  const body = (await readJson(req)) as { passphrase?: unknown } | null;
  const passphrase = typeof body?.passphrase === "string" ? body.passphrase : "";
  if (!(await verifyPassphrase(passphrase, auth.passphraseHash))) {
    await recordFailedLogin(kv, info.ip, auth.sessionSecret, now);
    rt.log.warn("login_failed");
    return jsonResponse({ error: "invalid_credentials" }, 401);
  }

  await clearFailedLogins(kv, info.ip, auth.sessionSecret);
  const { token, maxAgeSeconds } = await createSessionToken(auth, now);
  return jsonResponse({ ok: true }, 200, { "Set-Cookie": sessionCookie(req, token, maxAgeSeconds) });
}

/** POST /api/logout. */
export async function handleLogout(req: Request, _info: RequestInfo, _rt: Runtime): Promise<Response> {
  if (req.method !== "POST") return methodNotAllowed("POST");
  if (!isSameOrigin(req)) return jsonResponse({ error: "forbidden" }, 403);
  return jsonResponse({ ok: true }, 200, { "Set-Cookie": clearedSessionCookie(req) });
}

/**
 * The scheduled sync. Returns no counts and no detail, so even a direct hit on its function URL
 * reveals nothing; repeated hits are absorbed by the minimum attempt spacing in runRefresh.
 */
export async function handleScheduledRefresh(info: RequestInfo, rt: Runtime): Promise<Response> {
  const data = loadDataConfig(rt.env);
  if (!data.ok) {
    rt.log.error("data_config_invalid", { problems: data.problems.join("; ") });
    return new Response(null, { status: 202 });
  }
  const now = rt.now();
  const snapshots = new SnapshotStore(rt.openKv(snapshotStoreName(data.config.dataMode, info.deployContext)));
  const source = selectSource(data.config, rt.env, () => now);
  const outcome = await runRefresh({ config: data.config, source, snapshots, now: rt.now, sleep: rt.sleep, log: rt.log });
  rt.log.info("scheduled_refresh_done", { outcome: outcome.status });
  return new Response(null, { status: 202 });
}
