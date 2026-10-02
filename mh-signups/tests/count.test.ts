import { describe, expect, it } from "vitest";
import { AdapterError, type SignupRecord } from "../server/adapter.ts";
import { countSignups, resolveCounts } from "../server/count.ts";
import { createDemoSource, generateDemoLeads } from "../server/demo-source.ts";
import { collectPages } from "../server/pagination.ts";
import { computePeriodStarts } from "../shared/periods.ts";

const TZ = "America/New_York";
const asOf = new Date("2026-10-02T18:00:00Z"); // Fri Oct 2, 2:00 PM EDT
const starts = computePeriodStarts(asOf, TZ, "MONDAY");
const rec = (leadId: string, signedAt: string): SignupRecord => ({ leadId, signedAt });

describe("counting signups", () => {
  it("reports a true zero when the complete result has no qualifying records", () => {
    expect(countSignups([], starts, asOf)).toEqual({ today: 0, week: 0, month: 0 });
  });

  it("counts each lead once however many events it has", () => {
    const records = [
      rec("A", "2026-10-02T15:00:00Z"),
      rec("A", "2026-10-02T16:00:00Z"), // repeated signing
      rec("A", "2026-10-02T16:00:00Z"), // exact duplicate row
      rec("B", "2026-10-02T15:30:00Z"),
    ];
    expect(countSignups(records, starts, asOf)).toEqual({ today: 2, week: 2, month: 2 });
  });

  it("uses the earliest event, so a later re-signing cannot move a lead into a newer period", () => {
    const records = [rec("A", "2026-10-02T15:00:00Z"), rec("A", "2026-10-01T15:00:00Z")];
    expect(countSignups(records, starts, asOf)).toEqual({ today: 0, week: 1, month: 1 });
  });

  it("uses half-open intervals [periodStart, asOf)", () => {
    const records = [
      rec("at-day-start", "2026-10-02T04:00:00Z"), // counts: inclusive start
      rec("just-before-day", "2026-10-02T03:59:59Z"), // yesterday
      rec("at-cutoff", "2026-10-02T18:00:00Z"), // excluded: asOf is exclusive
      rec("after-cutoff", "2026-10-02T18:00:01Z"),
    ];
    expect(countSignups(records, starts, asOf)).toEqual({ today: 1, week: 2, month: 2 });
  });

  it("lets the week exceed the month when the week starts in the previous month", () => {
    const records = [
      rec("sep28", "2026-09-28T15:00:00Z"),
      rec("sep30", "2026-09-30T15:00:00Z"),
      rec("oct1", "2026-10-01T15:00:00Z"),
      rec("oct2", "2026-10-02T15:00:00Z"),
      rec("sep10", "2026-09-10T15:00:00Z"), // before the week and the month: ignored
    ];
    const counts = countSignups(records, starts, asOf);
    expect(counts).toEqual({ today: 1, week: 4, month: 2 });
    expect(counts.week).toBeGreaterThan(counts.month);
  });

  it("reads timestamps with explicit offsets correctly (UTC versus Eastern)", () => {
    const records = [rec("east", "2026-10-02T00:30:00-04:00"), rec("late-last-night", "2026-10-01T23:30:00-04:00")];
    expect(countSignups(records, starts, asOf)).toEqual({ today: 1, week: 2, month: 2 });
  });

  it("rejects timestamps without an offset instead of guessing a timezone", () => {
    expect(() => countSignups([rec("A", "2026-10-02T10:00:00")], starts, asOf)).toThrow(AdapterError);
    expect(() => countSignups([rec("A", "10/02/2026")], starts, asOf)).toThrow(AdapterError);
  });

  it("rejects records with no usable lead id", () => {
    expect(() => countSignups([rec("", "2026-10-02T10:00:00Z")], starts, asOf)).toThrow(AdapterError);
  });

  it("validates aggregate results from a source", () => {
    expect(resolveCounts({ type: "aggregates", today: 2, week: 9, month: 30 }, starts, asOf)).toEqual({ today: 2, week: 9, month: 30 });
    expect(() => resolveCounts({ type: "aggregates", today: -1, week: 1, month: 1 }, starts, asOf)).toThrow(AdapterError);
    expect(() => resolveCounts({ type: "aggregates", today: 1.5, week: 2, month: 3 }, starts, asOf)).toThrow(AdapterError);
    expect(() => resolveCounts({ type: "aggregates", today: 9, week: 3, month: 30 }, starts, asOf)).toThrow(AdapterError);
  });

  it("keeps signups whose lead was later closed, transferred, or lost (gross definition)", async () => {
    const now = new Date("2026-10-02T18:00:00Z");
    const leads = generateDemoLeads(now);
    const lost = leads.filter((l) => l.currentStatus !== "Signed" && Date.parse(l.signedAt) >= starts.week.getTime() && Date.parse(l.signedAt) < now.getTime());
    expect(lost.length).toBeGreaterThan(0); // the fixture really contains later-lost leads

    const result = await createDemoSource(() => now).fetchSignups({ from: starts.week, to: now, signal: new AbortController().signal });
    if (result.type !== "records") throw new Error("expected records");
    const ids = new Set(result.records.map((r) => r.leadId));
    for (const l of lost) expect(ids.has(l.leadId)).toBe(true);

    const expectedWeek = new Set(
      leads.filter((l) => Date.parse(l.signedAt) >= starts.week.getTime() && Date.parse(l.signedAt) < now.getTime()).map((l) => l.leadId),
    ).size;
    expect(countSignups(result.records, starts, now).week).toBe(expectedWeek); // repeats in the fixture collapse to one
  });
});

describe("complete pagination", () => {
  const pages = [
    { items: [1, 2], nextCursor: "p2" },
    { items: [3, 4], nextCursor: "p3" },
    { items: [5], nextCursor: null },
  ];

  it("reads every page", async () => {
    const seen: (string | null)[] = [];
    const all = await collectPages(async (cursor) => {
      seen.push(cursor);
      return pages[cursor === null ? 0 : Number(cursor.slice(1)) - 1]!;
    });
    expect(all).toEqual([1, 2, 3, 4, 5]);
    expect(seen).toEqual([null, "p2", "p3"]);
  });

  it("fails rather than returning a partial set when a later page fails", async () => {
    await expect(
      collectPages(async (cursor) => {
        if (cursor === "p3") throw new AdapterError("upstream");
        return pages[cursor === null ? 0 : 1]!;
      }),
    ).rejects.toMatchObject({ code: "upstream" });
  });

  it("fails when pagination never ends or a cursor repeats", async () => {
    await expect(collectPages(async () => ({ items: [1], nextCursor: "same" }))).rejects.toMatchObject({ code: "malformed" });
    let n = 0;
    await expect(collectPages(async () => ({ items: [1], nextCursor: `c${n++}` }), { maxPages: 5 })).rejects.toMatchObject({ code: "incomplete" });
  });

  it("fails on a malformed page", async () => {
    await expect(collectPages(async () => ({ items: "nope" as unknown as number[] }))).rejects.toMatchObject({ code: "malformed" });
  });
});
