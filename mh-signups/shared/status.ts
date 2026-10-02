import { periodStartsIso } from "./periods.ts";
import type { MetricKey, StatsResponse, StatsStatus } from "./stats.ts";

/** Counts older than this are labeled "Update delayed". */
export const STALE_AFTER_MS = 40 * 60 * 1000;

/**
 * Null out any metric whose saved period no longer matches the current calendar period.
 * Prevents yesterday's "today" (or last month's "month") from showing as the new total.
 * Idempotent; run on the server and again on the phone while the page stays open.
 */
export function applyPeriodGuard(stats: StatsResponse, now: Date): StatsResponse {
  if (stats.periodStarts === null) return stats;
  const expected = periodStartsIso(now, stats.timeZone, stats.weekStartsOn);
  const next = { ...stats };
  for (const key of ["today", "week", "month"] as MetricKey[]) {
    if (stats.periodStarts[key] !== expected[key]) next[key] = null;
  }
  return next;
}

/** Status as it should read right now, given how old the data cutoff is. */
export function effectiveStatus(stats: StatsResponse, now: Date): StatsStatus {
  if (stats.status === "unavailable") return "unavailable";
  if (stats.status === "delayed") return "delayed";
  if (stats.asOf !== null && now.getTime() - Date.parse(stats.asOf) > STALE_AFTER_MS) return "delayed";
  return "ok";
}
