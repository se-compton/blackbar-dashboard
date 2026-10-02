import { AdapterError, type SignupRecord, type SourceResult } from "./adapter.ts";
import { isIsoTimestamp, type MetricKey } from "../shared/stats.ts";

export type Counts = Record<MetricKey, number>;

/**
 * Count distinct leads per period over half-open intervals [periodStart, asOf).
 * - One lead counts once however many records or signatures it has (earliest signedAt wins).
 * - Status is not an input: a lead that later closed, transferred or was lost still counts.
 * - Records outside the window are ignored; invalid records fail the whole run.
 */
export function countSignups(records: SignupRecord[], starts: Record<MetricKey, Date>, asOf: Date): Counts {
  const firstSigned = new Map<string, number>();
  for (const r of records) {
    if (typeof r?.leadId !== "string" || r.leadId.trim() === "" || r.leadId.length > 200) {
      throw new AdapterError("malformed");
    }
    if (!isIsoTimestamp(r.signedAt)) throw new AdapterError("malformed");
    const t = Date.parse(r.signedAt);
    const prev = firstSigned.get(r.leadId);
    if (prev === undefined || t < prev) firstSigned.set(r.leadId, t);
  }
  const end = asOf.getTime();
  const counts: Counts = { today: 0, week: 0, month: 0 };
  for (const t of firstSigned.values()) {
    if (t >= end) continue;
    for (const key of ["today", "week", "month"] as MetricKey[]) {
      if (t >= starts[key].getTime()) counts[key] += 1;
    }
  }
  return counts;
}

/** Turn any adapter result into validated counts. */
export function resolveCounts(result: SourceResult, starts: Record<MetricKey, Date>, asOf: Date): Counts {
  if (result.type === "records") return countSignups(result.records, starts, asOf);
  const counts: Counts = { today: result.today, week: result.week, month: result.month };
  for (const v of Object.values(counts)) {
    if (!Number.isSafeInteger(v) || v < 0) throw new AdapterError("malformed");
  }
  // Today is a sub-interval of the week and of the month, so these must hold for any real source.
  if (counts.today > counts.week || counts.today > counts.month) throw new AdapterError("malformed");
  return counts;
}
