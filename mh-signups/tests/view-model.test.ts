import { describe, expect, it } from "vitest";
import { buildView, formatUpdated } from "../src/view-model.ts";
import type { StatsResponse } from "../shared/stats.ts";

const base: StatsResponse = {
  today: 7, week: 31, month: 118,
  timeZone: "America/New_York", weekStartsOn: "MONDAY",
  periodStarts: { today: "2026-10-02T04:00:00.000Z", week: "2026-09-28T04:00:00.000Z", month: "2026-10-01T04:00:00.000Z" },
  asOf: "2026-10-02T18:00:00.000Z", lastSuccessfulSyncAt: "2026-10-02T18:00:05.000Z",
  status: "ok", source: "leaddocket", unavailableReason: null,
};
const NOW = new Date("2026-10-02T18:10:00Z");
const view = (over: Partial<StatsResponse> = {}, extra: { offline?: boolean; now?: Date; phase?: "ready" | "loading" } = {}) =>
  buildView({ phase: extra.phase ?? "ready", stats: { ...base, ...over }, offline: extra.offline ?? false, now: extra.now ?? NOW });

describe("screen content", () => {
  it("shows exactly three metrics, in order, with the required labels", () => {
    const v = view();
    expect(v.metrics.map((m) => [m.key, m.label, m.value])).toEqual([
      ["today", "SIGNED TODAY", "7"],
      ["week", "SIGNED THIS WEEK", "31"],
      ["month", "SIGNED THIS MONTH", "118"],
    ]);
    expect(v.banners).toEqual([]);
    expect(v.demo).toBe(false);
  });

  it("formats large numbers with separators", () => {
    expect(view({ month: 1234 }).metrics[2]!.value).toBe("1,234");
  });

  it("distinguishes a true zero from unavailable", () => {
    const zero = view({ today: 0 }).metrics[0]!;
    expect(zero).toMatchObject({ value: "0", note: null });
    const unavailable = view({ today: null, week: null, month: null, periodStarts: null, asOf: null, lastSuccessfulSyncAt: null, status: "unavailable", unavailableReason: "waiting_first_update" });
    expect(unavailable.metrics.map((m) => [m.value, m.note])).toEqual([
      ["--", "Waiting for first update"], ["--", "Waiting for first update"], ["--", "Waiting for first update"],
    ]);
    expect(unavailable.footer).toBe("No update yet");
  });

  it("says 'Connection not configured' when live mode has no connection", () => {
    const v = view({ today: null, week: null, month: null, periodStarts: null, asOf: null, lastSuccessfulSyncAt: null, status: "unavailable", unavailableReason: "not_configured" });
    expect(v.metrics[0]).toMatchObject({ value: "--", note: "Connection not configured" });
  });

  it("labels demo data visibly", () => {
    expect(view({ source: "demo" }).demo).toBe(true);
  });

  it("footer shows the last successful update in Eastern time with the zone", () => {
    expect(view().footer).toBe("Updated Fri, Oct 2, 2:00 PM EDT");
    expect(formatUpdated("2026-12-15T17:30:00Z", "America/New_York")).toBe("Updated Tue, Dec 15, 12:30 PM EST");
    expect(formatUpdated("2026-03-08T07:30:00Z", "America/New_York")).toBe("Updated Sun, Mar 8, 3:30 AM EDT"); // just after spring forward
  });

  it("the footer time is the server's sync time, not the time the page was opened", () => {
    const later = view({}, { now: new Date("2026-10-02T18:30:00Z") });
    expect(later.footer).toBe(view().footer);
  });
});

describe("freshness and rollover on the phone", () => {
  it("shows 'Update delayed' when the cutoff is over 40 minutes old, keeping the numbers", () => {
    const v = view({}, { now: new Date("2026-10-02T18:41:00Z") });
    expect(v.banners).toEqual(["Update delayed"]);
    expect(v.metrics[0]!.value).toBe("7");
  });

  it("shows 'Update delayed' when the server reports a failed sync", () => {
    expect(view({ status: "delayed" }).banners).toEqual(["Update delayed"]);
  });

  it("replaces only the rolled-over metric with '--' and 'Waiting for update' at midnight", () => {
    const v = view({}, { now: new Date("2026-10-03T04:05:00Z") }); // Sat 12:05 AM EDT
    expect(v.metrics.map((m) => [m.value, m.note])).toEqual([["--", "Waiting for update"], ["31", null], ["118", null]]);
  });

  it("drops the month at month end and the week at Monday midnight", () => {
    expect(view({}, { now: new Date("2026-11-01T04:30:00Z") }).metrics.map((m) => m.value)).toEqual(["--", "--", "--"]); // Sun Nov 1 (week Mon Sep 28 vs Oct 26)
    const midweek = view({ periodStarts: { today: "2026-10-04T04:00:00.000Z", week: "2026-09-28T04:00:00.000Z", month: "2026-10-01T04:00:00.000Z" } }, { now: new Date("2026-10-05T04:05:00Z") });
    expect(midweek.metrics.map((m) => m.value)).toEqual(["--", "--", "118"]);
  });

  it("labels retained values as not current when offline", () => {
    const v = view({}, { offline: true });
    expect(v.banners).toContain("Offline. Numbers are not current.");
    expect(v.metrics[0]!.value).toBe("7"); // retained in memory, clearly labeled
  });

  it("shows '--' with an offline note when nothing was ever loaded", () => {
    const v = buildView({ phase: "ready", stats: null, offline: true, now: NOW });
    expect(v.metrics.every((m) => m.value === "--" && m.note === "Can't reach the server")).toBe(true);
  });

  it("shows a loading state, not zeros, before the first response", () => {
    const v = buildView({ phase: "loading", stats: null, offline: false, now: NOW });
    expect(v.metrics.every((m) => m.value === "--" && m.note === "Loading")).toBe(true);
  });
});
