import { beforeEach, describe, expect, it } from "vitest";
import { resetMemoryStores } from "../server/kv.ts";
import { runRefresh } from "../server/refresh.ts";
import { buildStats } from "../server/stats-service.ts";
import { applyPeriodGuard, effectiveStatus } from "../shared/status.ts";
import { parseStatsResponse } from "../shared/stats.ts";
import { DEMO_CONFIG, LIVE_CONFIG, store, stubSource } from "./helpers.ts";

beforeEach(() => resetMemoryStores());

async function seed(asOf: string, completedAt = asOf, records?: { leadId: string; signedAt: string }[]) {
  // Default record: signed one hour before the cutoff (a record stamped exactly at asOf is excluded by [start, asOf)).
  records ??= [{ leadId: "A", signedAt: new Date(Date.parse(asOf) - 3_600_000).toISOString() }];
  const s = store();
  const t = [new Date(asOf), new Date(completedAt)];
  let i = 0;
  await runRefresh({
    config: LIVE_CONFIG,
    snapshots: s.snapshots,
    source: stubSource(() => ({ type: "records", records })),
    now: () => t[Math.min(i++, 1)]!,
    sleep: async () => {},
    log: { info() {}, warn() {}, error() {} },
    minAttemptSpacingMs: 0,
  });
  return s.snapshots;
}

const stats = (snapshots: Awaited<ReturnType<typeof seed>>, now: string, config = LIVE_CONFIG) =>
  buildStats({ config, snapshots, now: new Date(now), sourceConfigured: true });

describe("stats response", () => {
  it("before any sync: unavailable, never zero, with the reason", async () => {
    const { snapshots } = store();
    const live = await buildStats({ config: LIVE_CONFIG, snapshots, now: new Date(), sourceConfigured: false });
    expect(live).toMatchObject({ today: null, week: null, month: null, status: "unavailable", unavailableReason: "not_configured", source: "leaddocket", asOf: null, lastSuccessfulSyncAt: null });
    const waiting = await buildStats({ config: LIVE_CONFIG, snapshots, now: new Date(), sourceConfigured: true });
    expect(waiting.unavailableReason).toBe("waiting_first_update");
    const demo = await buildStats({ config: DEMO_CONFIG, snapshots, now: new Date(), sourceConfigured: true });
    expect(demo.source).toBe("demo"); // a demo-mode answer is always labeled as demo
  });

  it("fresh data is ok and passes the typed validator", async () => {
    const snapshots = await seed("2026-10-02T18:00:00.000Z", "2026-10-02T18:00:04.000Z");
    const res = await stats(snapshots, "2026-10-02T18:10:00Z");
    expect(res).toMatchObject({ today: 1, week: 1, month: 1, status: "ok", asOf: "2026-10-02T18:00:00.000Z", lastSuccessfulSyncAt: "2026-10-02T18:00:04.000Z" });
    expect(parseStatsResponse(JSON.parse(JSON.stringify(res)))).toEqual(res);
  });

  it("becomes delayed when the cutoff is more than 40 minutes old, without changing timestamps", async () => {
    const snapshots = await seed("2026-10-02T18:00:00.000Z");
    expect((await stats(snapshots, "2026-10-02T18:40:00Z")).status).toBe("ok");
    const late = await stats(snapshots, "2026-10-02T18:41:00Z");
    expect(late.status).toBe("delayed");
    expect(late.today).toBe(1); // still the last good number
    expect(late.lastSuccessfulSyncAt).toBe("2026-10-02T18:00:00.000Z"); // not "refreshed" by the read
  });

  it("is delayed right after a known sync failure, keeping the original update time", async () => {
    const snapshots = await seed("2026-10-02T18:00:00.000Z");
    await runRefresh({
      config: LIVE_CONFIG,
      snapshots,
      source: stubSource(() => { throw new Error("x"); }),
      now: () => new Date("2026-10-02T18:30:00Z"),
      sleep: async () => {},
      log: { info() {}, warn() {}, error() {} },
      minAttemptSpacingMs: 0,
      maxAttempts: 1,
    });
    const res = await stats(snapshots, "2026-10-02T18:31:00Z");
    expect(res.status).toBe("delayed");
    expect(res.lastSuccessfulSyncAt).toBe("2026-10-02T18:00:00.000Z");
    expect(res.today).toBe(1);
  });

  it("a later successful sync clears the delayed state", async () => {
    const snapshots = await seed("2026-10-02T18:00:00.000Z");
    await runRefresh({
      config: LIVE_CONFIG, snapshots,
      source: stubSource(() => { throw new Error("x"); }),
      now: () => new Date("2026-10-02T18:30:00Z"), sleep: async () => {}, log: { info() {}, warn() {}, error() {} }, minAttemptSpacingMs: 0, maxAttempts: 1,
    });
    await runRefresh({
      config: LIVE_CONFIG, snapshots,
      source: stubSource(() => ({ type: "records", records: [] })),
      now: () => new Date("2026-10-02T19:00:00Z"), sleep: async () => {}, log: { info() {}, warn() {}, error() {} }, minAttemptSpacingMs: 0,
    });
    expect((await stats(snapshots, "2026-10-02T19:05:00Z")).status).toBe("ok");
  });

  describe("calendar rollover while the saved snapshot is old (for example during a failed sync)", () => {
    it("midnight: only today becomes unavailable", async () => {
      const snapshots = await seed("2026-10-02T03:50:00.000Z", undefined, [
        { leadId: "A", signedAt: "2026-10-01T15:00:00Z" }, { leadId: "B", signedAt: "2026-09-29T15:00:00Z" },
      ]); // Thu Oct 1, 11:50 PM EDT
      expect(await stats(snapshots, "2026-10-02T03:55:00Z")).toMatchObject({ today: 1, week: 2, month: 1 });
      const after = await stats(snapshots, "2026-10-02T04:10:00Z"); // 12:10 AM EDT Friday
      expect(after).toMatchObject({ today: null, week: 2, month: 1 });
    });

    it("month end: today and month become unavailable, the week that started last month does not", async () => {
      const snapshots = await seed("2026-10-01T03:50:00.000Z", undefined, [{ leadId: "A", signedAt: "2026-09-29T15:00:00Z" }]); // Wed Sep 30, 11:50 PM
      const after = await stats(snapshots, "2026-10-01T04:10:00Z"); // Thu Oct 1, 12:10 AM
      expect(after).toMatchObject({ today: null, week: 1, month: null });
    });

    it("Sunday night: the week becomes unavailable at Monday midnight", async () => {
      const snapshots = await seed("2026-10-05T03:50:00.000Z", undefined, [{ leadId: "A", signedAt: "2026-10-02T15:00:00Z" }]); // Sun Oct 4, 11:50 PM
      expect(await stats(snapshots, "2026-10-05T03:55:00Z")).toMatchObject({ week: 1 });
      expect(await stats(snapshots, "2026-10-05T04:05:00Z")).toMatchObject({ today: null, week: null, month: 1 });
    });

    it("new year: today and month become unavailable; the week spanning the year end is still valid", async () => {
      const snapshots = await seed("2027-01-01T04:50:00.000Z", undefined, [{ leadId: "A", signedAt: "2026-12-30T15:00:00Z" }]); // Thu Dec 31, 11:50 PM EST
      expect(await stats(snapshots, "2027-01-01T05:10:00Z")).toMatchObject({ today: null, week: 1, month: null }); // Fri Jan 1; week began Mon Dec 28
    });

    it("new week across a year end: Monday Jan 4 2027 drops the week", async () => {
      const snapshots = await seed("2027-01-04T04:50:00.000Z", undefined, [{ leadId: "A", signedAt: "2027-01-03T15:00:00Z" }]); // Sun Jan 3, 11:50 PM EST
      expect(await stats(snapshots, "2027-01-04T05:10:00Z")).toMatchObject({ today: null, week: null, month: 1 });
    });

    it("the phone applies the same guard to values it already holds", async () => {
      const snapshots = await seed("2026-10-02T03:50:00.000Z", undefined, [{ leadId: "A", signedAt: "2026-10-01T15:00:00Z" }]);
      const held = await stats(snapshots, "2026-10-02T03:55:00Z");
      expect(held.today).toBe(1);
      expect(applyPeriodGuard(held, new Date("2026-10-02T04:01:00Z")).today).toBeNull();
      expect(effectiveStatus(held, new Date("2026-10-02T04:30:00Z"))).toBe("ok"); // cutoff was 03:50Z: exactly 40 minutes
      expect(effectiveStatus(held, new Date("2026-10-02T04:31:00Z"))).toBe("delayed");
    });
  });

  it("changing WEEK_START invalidates the saved snapshot instead of mixing definitions", async () => {
    const snapshots = await seed("2026-10-02T18:00:00.000Z");
    const res = await stats(snapshots, "2026-10-02T18:05:00Z", { ...LIVE_CONFIG, weekStart: "SUNDAY" });
    expect(res).toMatchObject({ today: null, week: null, month: null, weekStartsOn: "SUNDAY", unavailableReason: "waiting_first_update" });
  });

  it("rejects malformed stats responses", () => {
    expect(() => parseStatsResponse({})).toThrow();
    expect(() => parseStatsResponse({ today: 1, week: 1, month: 1, timeZone: "x", weekStartsOn: "MONDAY", status: "ok", source: "demo", periodStarts: null, asOf: null, lastSuccessfulSyncAt: null })).toThrow(); // counts without period metadata
    expect(() => parseStatsResponse({ today: -1, week: null, month: null, timeZone: "x", weekStartsOn: "MONDAY", status: "ok", source: "demo", periodStarts: null, asOf: null, lastSuccessfulSyncAt: null })).toThrow();
    expect(() => parseStatsResponse({ today: null, week: null, month: null, timeZone: "x", weekStartsOn: "MONDAY", status: "ok", source: "demo", periodStarts: null, asOf: "2026-10-02 10:00", lastSuccessfulSyncAt: null })).toThrow();
  });
});
