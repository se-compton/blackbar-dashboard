import { describe, expect, it } from "vitest";
import { computePeriodStarts, localParts, periodStartsIso, retrievalStart } from "../shared/periods.ts";

const TZ = "America/New_York";
const iso = (d: Date) => d.toISOString();

describe("calendar periods in America/New_York", () => {
  it("converts UTC to Eastern local time", () => {
    expect(localParts(new Date("2026-10-02T16:30:00Z"), TZ)).toMatchObject({ month: 10, day: 2, hour: 12, minute: 30 });
    expect(localParts(new Date("2026-12-15T16:30:00Z"), TZ)).toMatchObject({ month: 12, day: 15, hour: 11, minute: 30 });
  });

  it("computes today, week (Monday) and month starts, and the week can start in the previous month", () => {
    // Friday Oct 2 2026, 10:30 EDT.
    const s = computePeriodStarts(new Date("2026-10-02T14:30:00Z"), TZ, "MONDAY");
    expect(iso(s.today)).toBe("2026-10-02T04:00:00.000Z"); // midnight EDT
    expect(iso(s.week)).toBe("2026-09-28T04:00:00.000Z"); // Monday Sep 28: previous month
    expect(iso(s.month)).toBe("2026-10-01T04:00:00.000Z");
    expect(iso(retrievalStart(s))).toBe("2026-09-28T04:00:00.000Z"); // earlier of week and month starts
  });

  it("honors Sunday versus Monday week start", () => {
    const asOf = new Date("2026-10-02T14:30:00Z");
    expect(iso(computePeriodStarts(asOf, TZ, "MONDAY").week)).toBe("2026-09-28T04:00:00.000Z");
    expect(iso(computePeriodStarts(asOf, TZ, "SUNDAY").week)).toBe("2026-09-27T04:00:00.000Z");
    // On a Sunday itself.
    const sunday = new Date("2026-10-04T16:00:00Z");
    expect(iso(computePeriodStarts(sunday, TZ, "SUNDAY").week)).toBe("2026-10-04T04:00:00.000Z");
    expect(iso(computePeriodStarts(sunday, TZ, "MONDAY").week)).toBe("2026-09-28T04:00:00.000Z");
  });

  it("flips the day exactly at local midnight, not at UTC midnight", () => {
    expect(iso(computePeriodStarts(new Date("2026-10-02T03:59:59Z"), TZ, "MONDAY").today)).toBe("2026-10-01T04:00:00.000Z");
    expect(iso(computePeriodStarts(new Date("2026-10-02T04:00:00Z"), TZ, "MONDAY").today)).toBe("2026-10-02T04:00:00.000Z");
    // 8pm Eastern is already the next day in UTC; the local day must not change.
    expect(iso(computePeriodStarts(new Date("2026-10-03T00:00:00Z"), TZ, "MONDAY").today)).toBe("2026-10-02T04:00:00.000Z");
  });

  it("handles the spring-forward change (Sun Mar 8 2026): a 23-hour day, week spans the change", () => {
    const sundayNoon = new Date("2026-03-08T16:00:00Z"); // 12:00 EDT
    const s = computePeriodStarts(sundayNoon, TZ, "MONDAY");
    expect(iso(s.today)).toBe("2026-03-08T05:00:00.000Z"); // midnight EST, before the change
    expect(iso(s.week)).toBe("2026-03-02T05:00:00.000Z"); // Monday in EST
    const monday = computePeriodStarts(new Date("2026-03-09T12:00:00Z"), TZ, "MONDAY");
    expect(iso(monday.today)).toBe("2026-03-09T04:00:00.000Z"); // midnight EDT, after the change
    expect(iso(monday.week)).toBe("2026-03-09T04:00:00.000Z");
    // The day that contains the change is 23 hours long.
    expect(monday.today.getTime() - s.today.getTime()).toBe(23 * 3600_000);
  });

  it("handles the fall-back change (Sun Nov 1 2026): a 25-hour day", () => {
    const sunday = computePeriodStarts(new Date("2026-11-01T18:00:00Z"), TZ, "MONDAY");
    expect(iso(sunday.today)).toBe("2026-11-01T04:00:00.000Z"); // midnight EDT
    expect(iso(sunday.week)).toBe("2026-10-26T04:00:00.000Z");
    const monday = computePeriodStarts(new Date("2026-11-02T15:00:00Z"), TZ, "MONDAY");
    expect(iso(monday.today)).toBe("2026-11-02T05:00:00.000Z"); // midnight EST
    expect(monday.today.getTime() - sunday.today.getTime()).toBe(25 * 3600_000);
    // Just after the repeated 1am hour is still Sunday Nov 1.
    expect(iso(computePeriodStarts(new Date("2026-11-01T06:30:00Z"), TZ, "MONDAY").today)).toBe("2026-11-01T04:00:00.000Z");
  });

  it("crosses a month boundary at local midnight", () => {
    const before = computePeriodStarts(new Date("2026-10-01T03:59:00Z"), TZ, "MONDAY"); // Sep 30 23:59 EDT
    const after = computePeriodStarts(new Date("2026-10-01T04:01:00Z"), TZ, "MONDAY"); // Oct 1 00:01 EDT
    expect(iso(before.month)).toBe("2026-09-01T04:00:00.000Z");
    expect(iso(after.month)).toBe("2026-10-01T04:00:00.000Z");
    expect(iso(after.week)).toBe("2026-09-28T04:00:00.000Z"); // same week, still started in September
  });

  it("crosses a year boundary, with the week starting in the previous year", () => {
    const eve = computePeriodStarts(new Date("2027-01-01T04:59:00Z"), TZ, "MONDAY"); // Dec 31 23:59 EST
    expect(iso(eve.today)).toBe("2026-12-31T05:00:00.000Z");
    expect(iso(eve.month)).toBe("2026-12-01T05:00:00.000Z");
    const newYear = computePeriodStarts(new Date("2027-01-01T17:00:00Z"), TZ, "MONDAY");
    expect(iso(newYear.today)).toBe("2027-01-01T05:00:00.000Z");
    expect(iso(newYear.month)).toBe("2027-01-01T05:00:00.000Z");
    expect(iso(newYear.week)).toBe("2026-12-28T05:00:00.000Z");
    expect(iso(retrievalStart(newYear))).toBe("2026-12-28T05:00:00.000Z");
  });

  it("ignores the server's own timezone", () => {
    const original = process.env.TZ;
    try {
      process.env.TZ = "Pacific/Auckland";
      expect(periodStartsIso(new Date("2026-10-02T14:30:00Z"), TZ, "MONDAY").today).toBe("2026-10-02T04:00:00.000Z");
    } finally {
      if (original === undefined) delete process.env.TZ;
      else process.env.TZ = original;
    }
  });

  it("works for another zone that does not observe daylight saving", () => {
    expect(periodStartsIso(new Date("2026-10-02T14:30:00Z"), "America/Phoenix", "MONDAY").today).toBe("2026-10-02T07:00:00.000Z");
  });
});
