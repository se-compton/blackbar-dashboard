import { parseStatsResponse, type StatsResponse } from "../shared/stats.ts";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export type StatsOutcome =
  | { kind: "ok"; stats: StatsResponse }
  | { kind: "unauthorized" }
  | { kind: "auth_unconfigured" }
  | { kind: "network_error" }
  | { kind: "bad_response" };

const TIMEOUT_MS = 10_000;

async function request(fetchFn: FetchLike, url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetchFn(url, { ...init, signal: controller.signal, credentials: "same-origin", cache: "no-store" });
  } finally {
    clearTimeout(timer);
  }
}

/** Reads the saved snapshot through our own endpoint. Never reaches Lead Docket. */
export async function fetchStats(fetchFn: FetchLike): Promise<StatsOutcome> {
  let res: Response;
  try {
    res = await request(fetchFn, "/api/stats", { method: "GET", headers: { Accept: "application/json" } });
  } catch {
    return { kind: "network_error" };
  }
  if (res.status === 401) return { kind: "unauthorized" };
  if (res.status === 503) return { kind: "auth_unconfigured" };
  if (!res.ok) return { kind: "network_error" };
  try {
    return { kind: "ok", stats: parseStatsResponse(await res.json()) };
  } catch {
    return { kind: "bad_response" };
  }
}

export type LoginOutcome =
  | { kind: "ok" }
  | { kind: "invalid" }
  | { kind: "throttled"; retryAfterSeconds: number }
  | { kind: "unconfigured" }
  | { kind: "network_error" };

export async function login(fetchFn: FetchLike, passphrase: string): Promise<LoginOutcome> {
  let res: Response;
  try {
    res = await request(fetchFn, "/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    });
  } catch {
    return { kind: "network_error" };
  }
  if (res.ok) return { kind: "ok" };
  if (res.status === 401) return { kind: "invalid" };
  if (res.status === 429) {
    const body = (await res.json().catch(() => ({}))) as { retryAfterSeconds?: number };
    return { kind: "throttled", retryAfterSeconds: body.retryAfterSeconds ?? 900 };
  }
  if (res.status === 503) return { kind: "unconfigured" };
  return { kind: "network_error" };
}

export async function logout(fetchFn: FetchLike): Promise<void> {
  try {
    await request(fetchFn, "/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  } catch {
    /* the session cookie expires on its own; nothing to retry */
  }
}
