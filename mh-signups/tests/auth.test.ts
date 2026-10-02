import { SignJWT } from "jose";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { handleLogin, handleLogout, handleScheduledRefresh, handleStats } from "../server/handlers.ts";
import { resetMemoryStores } from "../server/kv.ts";
import { AUTH_ENV, cookieFrom, loginRequest, ORIGIN, PASSPHRASE, request, runtime, SESSION_SECRET } from "./helpers.ts";

import loginFn from "../netlify/functions/login.mts";
import logoutFn from "../netlify/functions/logout.mts";
import refreshFn from "../netlify/functions/refresh.mts";
import statsFn from "../netlify/functions/stats.mts";

const info = { ip: "203.0.113.7" };
const DAY = 86_400_000;
const T0 = new Date("2026-10-02T14:00:00Z");

beforeEach(() => resetMemoryStores());

async function login(env: Record<string, string | undefined> = { ...AUTH_ENV, DATA_MODE: "demo" }, now = T0) {
  const res = await handleLogin(loginRequest(PASSPHRASE), info, runtime({ env, now: () => now }));
  return { res, cookie: cookieFrom(res) };
}

const getStats = (cookie: string | null, env: Record<string, string | undefined>, now = T0) =>
  handleStats(request("/api/stats", cookie ? { headers: { cookie } } : {}), info, runtime({ env, now: () => now }));

describe("login and sessions", () => {
  it("accepts the right passphrase and sets a hardened cookie", async () => {
    const { res } = await login();
    expect(res.status).toBe(200);
    const setCookie = res.headers.get("set-cookie")!;
    expect(setCookie).toMatch(/^__Host-mh_session=[\w-]+\.[\w-]+\.[\w-]+;/);
    expect(setCookie).toContain("HttpOnly");
    expect(setCookie).toContain("Secure");
    expect(setCookie).toContain("SameSite=Strict");
    expect(setCookie).toContain("Path=/");
    expect(setCookie).toContain(`Max-Age=${30 * 86400}`); // default 30 days
    expect(setCookie).not.toContain("Domain");
    expect(await res.json()).toEqual({ ok: true }); // the token is never in the body
    expect(res.headers.get("cache-control")).toBe("private, no-store");
  });

  it("honors SESSION_DAYS", async () => {
    const { res } = await login({ ...AUTH_ENV, DATA_MODE: "demo", SESSION_DAYS: "7" });
    expect(res.headers.get("set-cookie")).toContain(`Max-Age=${7 * 86400}`);
  });

  it("rejects a wrong passphrase with a generic answer and no cookie", async () => {
    const res = await handleLogin(loginRequest("wrong passphrase!!"), info, runtime({ env: AUTH_ENV }));
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await res.json()).toEqual({ error: "invalid_credentials" });
  });

  it("serves stats with a valid session, no-store, same-origin only", async () => {
    const env = { ...AUTH_ENV, DATA_MODE: "demo" };
    const { cookie } = await login(env);
    const res = await getStats(cookie, env);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(res.headers.get("access-control-allow-origin")).toBeNull();
  });

  it("expires sessions on schedule", async () => {
    const env = { ...AUTH_ENV, DATA_MODE: "demo" };
    const { cookie } = await login(env, T0);
    expect((await getStats(cookie, env, new Date(T0.getTime() + 29 * DAY))).status).toBe(200);
    expect((await getStats(cookie, env, new Date(T0.getTime() + 30 * DAY + 2000))).status).toBe(401);
  });

  it("rotating SESSION_SECRET revokes every outstanding session", async () => {
    const env = { ...AUTH_ENV, DATA_MODE: "demo" };
    const { cookie } = await login(env);
    expect((await getStats(cookie, env)).status).toBe(200);
    expect((await getStats(cookie, { ...env, SESSION_SECRET: "rotated-".padEnd(48, "y") })).status).toBe(401);
  });

  it("rejects tampered, unsigned, foreign-signed, and malformed tokens", async () => {
    const env = { ...AUTH_ENV, DATA_MODE: "demo" };
    const { cookie } = await login(env);
    const token = cookie.split("=")[1]!;
    const name = "__Host-mh_session";
    expect((await getStats(`${name}=${token.slice(0, -2)}xx`, env)).status).toBe(401);
    const unsigned = `${Buffer.from('{"alg":"none"}').toString("base64url")}.${Buffer.from('{"iss":"mh-signups","aud":"mh-signups-viewer","exp":9999999999}').toString("base64url")}.`;
    expect((await getStats(`${name}=${unsigned}`, env)).status).toBe(401);
    const foreign = await new SignJWT({}).setProtectedHeader({ alg: "HS256" }).setIssuer("mh-signups").setAudience("mh-signups-viewer")
      .setExpirationTime("1h").sign(new TextEncoder().encode("attacker-secret".padEnd(48, "z")));
    expect((await getStats(`${name}=${foreign}`, env)).status).toBe(401);
    expect((await getStats(`${name}=garbage`, env)).status).toBe(401);
    expect((await getStats(`mh_session=${token}`, env)).status).toBe(401); // wrong cookie name on https
  });

  it("logout clears the cookie", async () => {
    const res = await handleLogout(request("/api/logout", { method: "POST" }), info, runtime());
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toMatch(/Max-Age=0/);
  });
});

describe("same-origin protection on login and logout", () => {
  const env = { ...AUTH_ENV, DATA_MODE: "demo" };
  it("rejects a missing Origin, a foreign Origin, and cross-site fetches", async () => {
    for (const extra of [{ origin: null }, { origin: "https://evil.example" }, { headers: { "sec-fetch-site": "cross-site" } }]) {
      const res = await handleLogin(loginRequest(PASSPHRASE, extra), info, runtime({ env }));
      expect(res.status).toBe(403);
      expect(res.headers.get("set-cookie")).toBeNull();
    }
    expect((await handleLogout(request("/api/logout", { method: "POST", origin: "https://evil.example" }), info, runtime())).status).toBe(403);
  });

  it("does not take a passphrase from a non-JSON body or the URL", async () => {
    const form = request("/api/login", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `passphrase=${PASSPHRASE}` });
    expect((await handleLogin(form, info, runtime({ env }))).status).toBe(401);
    const url = request(`/api/login?passphrase=${encodeURIComponent(PASSPHRASE)}`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    expect((await handleLogin(url, info, runtime({ env }))).status).toBe(401);
  });

  it("only accepts POST for login and logout", async () => {
    expect((await handleLogin(request("/api/login"), info, runtime({ env }))).status).toBe(405);
    expect((await handleLogout(request("/api/logout"), info, runtime())).status).toBe(405);
    expect((await handleStats(request("/api/stats", { method: "POST" }), info, runtime({ env }))).status).toBe(405);
  });
});

describe("failed-login throttling", () => {
  const env = { ...AUTH_ENV, DATA_MODE: "demo" };
  const bad = (rt = runtime({ env }), ip = "198.51.100.1") => handleLogin(loginRequest("nope nope nope nope"), { ip }, rt);

  it("locks out after repeated failures, even for the correct passphrase, and shares state across instances", async () => {
    for (let i = 0; i < 5; i++) expect((await bad(runtime({ env }))).status).toBe(401); // each call is a separate "instance"
    const locked = await handleLogin(loginRequest(PASSPHRASE), { ip: "198.51.100.1" }, runtime({ env })); // a fresh instance
    expect(locked.status).toBe(429);
    expect(Number(locked.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(locked.headers.get("set-cookie")).toBeNull();
  });

  it("does not lock out other clients, and unlocks after the window", async () => {
    for (let i = 0; i < 5; i++) await bad();
    const other = await handleLogin(loginRequest(PASSPHRASE), { ip: "198.51.100.99" }, runtime({ env }));
    expect(other.status).toBe(200);
    const later = await handleLogin(loginRequest(PASSPHRASE), { ip: "198.51.100.1" }, runtime({ env, now: () => new Date(Date.now() + 16 * 60_000) }));
    expect(later.status).toBe(200);
  });
});

describe("fail closed and no unauthenticated counts", () => {
  const live = { DATA_MODE: "live" };

  it("requires a session in live mode and reveals no counts without one", async () => {
    const res = await getStats(null, { ...live, ...AUTH_ENV });
    expect(res.status).toBe(401);
    const text = await res.text();
    expect(text).not.toMatch(/today|week|month|asOf/);
    expect(res.headers.get("cache-control")).toBe("private, no-store");
  });

  it("serves nothing in live mode when authentication settings are missing or partial", async () => {
    for (const env of [live, { ...live, SESSION_SECRET }, { ...live, DASHBOARD_PASSPHRASE_HASH: AUTH_ENV.DASHBOARD_PASSPHRASE_HASH }, { ...live, ...AUTH_ENV, SESSION_SECRET: "short" }]) {
      const res = await getStats(null, env);
      expect([503]).toContain(res.status);
      expect(await res.text()).not.toMatch(/today|week|month/);
    }
  });

  it("refuses to start on a mangled hash (for example '$' stripped by a shell)", async () => {
    const mangled = (AUTH_ENV.DASHBOARD_PASSPHRASE_HASH as string).replaceAll("$", "");
    expect((await getStats(null, { ...live, SESSION_SECRET, DASHBOARD_PASSPHRASE_HASH: mangled })).status).toBe(503);
  });

  it("fails closed on an unrecognized DATA_MODE", async () => {
    expect((await getStats(null, { DATA_MODE: "prod" })).status).toBe(503);
  });

  it("in demo mode, requires a session as soon as authentication is configured", async () => {
    expect((await getStats(null, { DATA_MODE: "demo", ...AUTH_ENV })).status).toBe(401);
  });

  it("in demo mode with no authentication configured, serves only synthetic data labeled demo", async () => {
    const res = await getStats(null, { DATA_MODE: "demo" });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ source: "demo" });
  });

  it("live mode with missing Lead Docket settings shows 'not configured', never demo numbers", async () => {
    const env = { ...live, ...AUTH_ENV };
    const { cookie } = await login(env);
    const body = await (await getStats(cookie, env)).json();
    expect(body).toMatchObject({ today: null, week: null, month: null, source: "leaddocket", status: "unavailable", unavailableReason: "not_configured" });
  });

  it("live mode never lazily syncs from a page request", async () => {
    const env = { ...live, ...AUTH_ENV, LEAD_DOCKET_BASE_URL: "https://example.invalid", LEAD_DOCKET_API_KEY: "k".repeat(20) };
    const { cookie } = await login(env);
    const body = await (await getStats(cookie, env)).json();
    expect(body).toMatchObject({ status: "unavailable", unavailableReason: "waiting_first_update" });
  });
});

describe("direct function routes enforce the same rules", () => {
  const saved = { ...process.env };
  const ctx = { ip: "203.0.113.50", deploy: { context: "production" } } as never;
  beforeEach(() => {
    Object.assign(process.env, { DATA_MODE: "live", ...AUTH_ENV });
    delete process.env.LEAD_DOCKET_BASE_URL;
    delete process.env.LEAD_DOCKET_API_KEY;
  });
  afterEach(() => {
    for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k];
    Object.assign(process.env, saved);
  });

  it("stats function: 401 without a session, via the function export itself", async () => {
    const res = await statsFn(request("/.netlify/functions/stats"), ctx);
    expect(res.status).toBe(401);
    expect(await res.text()).not.toMatch(/today|week|month/);
  });

  it("stats function: 401 with a forged cookie", async () => {
    const res = await statsFn(request("/api/stats", { headers: { cookie: "__Host-mh_session=abc.def.ghi" } }), ctx);
    expect(res.status).toBe(401);
  });

  it("login then stats through the function exports", async () => {
    const res = await loginFn(loginRequest(PASSPHRASE), ctx);
    expect(res.status).toBe(200);
    const stats = await statsFn(request("/api/stats", { headers: { cookie: cookieFrom(res) } }), ctx);
    expect(stats.status).toBe(200);
  });

  it("login and logout functions apply the origin check", async () => {
    expect((await loginFn(loginRequest(PASSPHRASE, { origin: "https://evil.example" }), ctx)).status).toBe(403);
    expect((await logoutFn(request("/api/logout", { method: "POST", origin: null }), ctx)).status).toBe(403);
  });

  it("the scheduled function returns nothing about counts", async () => {
    const res = await refreshFn(new Request(`${ORIGIN}/.netlify/functions/refresh`, { method: "POST", body: '{"next_run":"x"}' }), ctx);
    expect(res.status).toBe(202);
    expect(await res.text()).toBe("");
  });

  it("scheduled refresh handler reports nothing and refuses bad configuration quietly", async () => {
    const res = await handleScheduledRefresh({ deployContext: "production" }, runtime({ env: { DATA_MODE: "nonsense" } }));
    expect(res.status).toBe(202);
  });
});
