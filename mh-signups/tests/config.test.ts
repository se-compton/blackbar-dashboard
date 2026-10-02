import { beforeEach, describe, expect, it } from "vitest";
import { authRequired, loadAuthConfig, loadDataConfig, snapshotStoreName } from "../server/config.ts";
import { handleScheduledRefresh, handleStats } from "../server/handlers.ts";
import { memoryKv, resetMemoryStores } from "../server/kv.ts";
import { SnapshotStore } from "../server/snapshot-store.ts";
import { AUTH_ENV, request, runtime, SESSION_SECRET } from "./helpers.ts";

beforeEach(() => resetMemoryStores());

describe("configuration", () => {
  it("applies the documented defaults", () => {
    expect(loadDataConfig({})).toEqual({ ok: true, config: { dataMode: "demo", timeZone: "America/New_York", weekStart: "MONDAY" } });
  });

  it("accepts live, Sunday, and another IANA zone", () => {
    expect(loadDataConfig({ DATA_MODE: "LIVE", WEEK_START: "sunday", DASHBOARD_TIMEZONE: "America/Chicago" })).toEqual({
      ok: true,
      config: { dataMode: "live", timeZone: "America/Chicago", weekStart: "SUNDAY" },
    });
  });

  it("rejects typos instead of silently choosing a default", () => {
    expect(loadDataConfig({ DATA_MODE: "prod" })).toMatchObject({ ok: false });
    expect(loadDataConfig({ WEEK_START: "TUESDAY" })).toMatchObject({ ok: false });
    expect(loadDataConfig({ DASHBOARD_TIMEZONE: "Eastern" })).toMatchObject({ ok: false });
  });

  it("validates authentication settings", () => {
    expect(loadAuthConfig({})).toEqual({ kind: "disabled" });
    expect(loadAuthConfig(AUTH_ENV)).toMatchObject({ kind: "enabled", sessionDays: 30 });
    expect(loadAuthConfig({ ...AUTH_ENV, SESSION_DAYS: "90" })).toMatchObject({ kind: "enabled", sessionDays: 90 });
    for (const bad of ["0", "366", "x", "1.5"]) expect(loadAuthConfig({ ...AUTH_ENV, SESSION_DAYS: bad }).kind).toBe("invalid");
    expect(loadAuthConfig({ SESSION_SECRET }).kind).toBe("invalid");
    expect(loadAuthConfig({ DASHBOARD_PASSPHRASE_HASH: "plaintext-password", SESSION_SECRET }).kind).toBe("invalid");
  });

  it("live mode requires authentication; demo only once configured", () => {
    expect(authRequired("live", { kind: "disabled" })).toBe(true);
    expect(authRequired("demo", { kind: "disabled" })).toBe(false);
    expect(authRequired("demo", loadAuthConfig(AUTH_ENV))).toBe(true);
  });
});

describe("demo and production snapshot isolation", () => {
  it("uses different namespaces per mode and per deploy context, none tied to a deploy id", () => {
    const names = [
      snapshotStoreName("live", "production"),
      snapshotStoreName("demo", "production"),
      snapshotStoreName("live", "deploy-preview"),
      snapshotStoreName("demo", "branch-deploy"),
      snapshotStoreName("demo", undefined),
    ];
    expect(new Set(names).size).toBe(names.length);
    expect(snapshotStoreName("live", "production")).toBe("mh-signups-live"); // stable across redeploys
    expect(snapshotStoreName("live", "deploy-preview")).not.toBe(snapshotStoreName("live", "production"));
  });

  it("a demo snapshot is never visible to live mode, and vice versa", async () => {
    const env = { ...AUTH_ENV };
    // Demo mode with auth configured: sign in is covered elsewhere; use the scheduled path to write a demo snapshot.
    await handleScheduledRefresh({ deployContext: "production" }, runtime({ env: { ...env, DATA_MODE: "demo" } }));
    expect(await new SnapshotStore(memoryKv("mh-signups-demo")).readSnapshot()).not.toBeNull();
    expect(await new SnapshotStore(memoryKv("mh-signups-live")).readSnapshot()).toBeNull();

    // Live mode (unconfigured Lead Docket) reads its own, empty, namespace: no demo numbers leak in.
    const { cookie } = await (async () => {
      const { handleLogin } = await import("../server/handlers.ts");
      const { loginRequest, cookieFrom, PASSPHRASE } = await import("./helpers.ts");
      const res = await handleLogin(loginRequest(PASSPHRASE), {}, runtime({ env: { ...env, DATA_MODE: "live" } }));
      return { cookie: cookieFrom(res) };
    })();
    const res = await handleStats(request("/api/stats", { headers: { cookie } }), { deployContext: "production" }, runtime({ env: { ...env, DATA_MODE: "live" } }));
    expect(await res.json()).toMatchObject({ source: "leaddocket", today: null });
  });

  it("a failed live sync in production does not touch the demo namespace", async () => {
    await handleScheduledRefresh({ deployContext: "production" }, runtime({ env: { DATA_MODE: "live" } }));
    expect(await new SnapshotStore(memoryKv("mh-signups-demo")).readSnapshot()).toBeNull();
    expect((await new SnapshotStore(memoryKv("mh-signups-live")).readSyncState()).lastFailureCode).toBe("not_configured");
  });
});
