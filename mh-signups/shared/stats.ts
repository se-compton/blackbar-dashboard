/**
 * Application-owned stats contract. This is NOT a representation of Lead Docket's API.
 * Used by the server (to validate what it sends) and by the phone (to validate what it receives).
 */

export type WeekStart = "MONDAY" | "SUNDAY";
export type StatsStatus = "ok" | "delayed" | "unavailable";
export type StatsSource = "demo" | "leaddocket";
export type MetricKey = "today" | "week" | "month";

export interface PeriodStarts {
  /** Start of the local calendar day, as a UTC instant (ISO with Z). */
  today: string;
  /** Start of the local week (Monday by default), as a UTC instant. */
  week: string;
  /** First day of the local calendar month at local midnight, as a UTC instant. */
  month: string;
}

export interface StatsResponse {
  /** Distinct signups in [periodStarts.today, asOf). null means unavailable, never zero. */
  today: number | null;
  /** Distinct signups in [periodStarts.week, asOf). null means unavailable. */
  week: number | null;
  /** Distinct signups in [periodStarts.month, asOf). null means unavailable. */
  month: number | null;
  /** IANA zone used for every calendar boundary. */
  timeZone: string;
  weekStartsOn: WeekStart;
  /** Period identifiers the saved counts were calculated for. null before the first successful sync. */
  periodStarts: PeriodStarts | null;
  /** Common data cutoff for all three totals. Not the time the phone asked. */
  asOf: string | null;
  /** When the last complete, validated sync finished. Never moves on a failed sync or a page load. */
  lastSuccessfulSyncAt: string | null;
  /** ok: fresh. delayed: counts are present but a sync failed or the cutoff is over 40 minutes old. unavailable: no counts. */
  status: StatsStatus;
  source: StatsSource;
  /** Why counts are missing when no snapshot exists. null when a snapshot is present. */
  unavailableReason: "not_configured" | "waiting_first_update" | null;
}

const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

export function isIsoTimestamp(value: unknown): value is string {
  return typeof value === "string" && ISO_WITH_OFFSET.test(value) && !Number.isNaN(Date.parse(value));
}

function isCount(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isSafeInteger(value) && value >= 0);
}

function fail(message: string): never {
  throw new Error(`Invalid stats response: ${message}`);
}

/** Validate untrusted JSON as a StatsResponse. Throws on any deviation. */
export function parseStatsResponse(input: unknown): StatsResponse {
  if (typeof input !== "object" || input === null) fail("not an object");
  const o = input as Record<string, unknown>;
  if (!isCount(o.today) || !isCount(o.week) || !isCount(o.month)) fail("counts");
  if (typeof o.timeZone !== "string" || o.timeZone === "") fail("timeZone");
  if (o.weekStartsOn !== "MONDAY" && o.weekStartsOn !== "SUNDAY") fail("weekStartsOn");
  if (o.status !== "ok" && o.status !== "delayed" && o.status !== "unavailable") fail("status");
  if (o.source !== "demo" && o.source !== "leaddocket") fail("source");
  const reason = o.unavailableReason ?? null;
  if (reason !== null && reason !== "not_configured" && reason !== "waiting_first_update") fail("unavailableReason");

  let periodStarts: PeriodStarts | null = null;
  if (o.periodStarts !== null && o.periodStarts !== undefined) {
    const p = o.periodStarts as Record<string, unknown>;
    if (typeof p !== "object" || !isIsoTimestamp(p.today) || !isIsoTimestamp(p.week) || !isIsoTimestamp(p.month)) {
      fail("periodStarts");
    }
    periodStarts = { today: p.today as string, week: p.week as string, month: p.month as string };
  }
  const asOf = o.asOf ?? null;
  const last = o.lastSuccessfulSyncAt ?? null;
  if (asOf !== null && !isIsoTimestamp(asOf)) fail("asOf");
  if (last !== null && !isIsoTimestamp(last)) fail("lastSuccessfulSyncAt");
  if ((o.today !== null || o.week !== null || o.month !== null) && (periodStarts === null || asOf === null)) {
    fail("counts without period metadata");
  }

  return {
    today: o.today,
    week: o.week,
    month: o.month,
    timeZone: o.timeZone,
    weekStartsOn: o.weekStartsOn,
    periodStarts,
    asOf,
    lastSuccessfulSyncAt: last,
    status: o.status,
    source: o.source,
    unavailableReason: reason,
  };
}
