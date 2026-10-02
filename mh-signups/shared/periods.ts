import type { MetricKey, PeriodStarts, WeekStart } from "./stats.ts";

/**
 * Calendar math in a named IANA zone. Never uses a fixed UTC offset, so daylight
 * saving changes are handled by the platform's tz database.
 */

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
    formatters.set(timeZone, f);
  }
  return f;
}

export function isValidTimeZone(timeZone: string): boolean {
  try {
    formatterFor(timeZone);
    return true;
  } catch {
    return false;
  }
}

export interface LocalParts {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
}

export function localParts(instant: Date, timeZone: string): LocalParts {
  const parts: Record<string, number> = {};
  for (const p of formatterFor(timeZone).formatToParts(instant)) {
    if (p.type !== "literal") parts[p.type] = Number(p.value);
  }
  return {
    year: parts.year!,
    month: parts.month!,
    day: parts.day!,
    hour: parts.hour! % 24,
    minute: parts.minute!,
    second: parts.second!,
  };
}

/** Offset of the zone from UTC at an instant, in ms (positive east of UTC). */
function offsetMs(instantMs: number, timeZone: string): number {
  const whole = Math.floor(instantMs / 1000) * 1000;
  const p = localParts(new Date(whole), timeZone);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - whole;
}

/** The UTC instant at which the zone's wall clock first reads 00:00:00 on the given local date. */
export function localMidnightUtc(year: number, month: number, day: number, timeZone: string): Date {
  const naive = Date.UTC(year, month - 1, day);
  const first = naive - offsetMs(naive, timeZone);
  const second = naive - offsetMs(first, timeZone);
  return new Date(second);
}

export function computePeriodStarts(asOf: Date, timeZone: string, weekStart: WeekStart): Record<MetricKey, Date> {
  const p = localParts(asOf, timeZone);
  const weekday = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay(); // 0 = Sunday
  const firstDay = weekStart === "MONDAY" ? 1 : 0;
  const back = (weekday - firstDay + 7) % 7;
  const weekDate = new Date(Date.UTC(p.year, p.month - 1, p.day - back));
  return {
    today: localMidnightUtc(p.year, p.month, p.day, timeZone),
    week: localMidnightUtc(weekDate.getUTCFullYear(), weekDate.getUTCMonth() + 1, weekDate.getUTCDate(), timeZone),
    month: localMidnightUtc(p.year, p.month, 1, timeZone),
  };
}

export function periodStartsIso(asOf: Date, timeZone: string, weekStart: WeekStart): PeriodStarts {
  const s = computePeriodStarts(asOf, timeZone, weekStart);
  return { today: s.today.toISOString(), week: s.week.toISOString(), month: s.month.toISOString() };
}

/** Earliest instant needed to cover the week and the month. A week can start in the previous month or year. */
export function retrievalStart(starts: Record<MetricKey, Date>): Date {
  return new Date(Math.min(starts.week.getTime(), starts.month.getTime(), starts.today.getTime()));
}
