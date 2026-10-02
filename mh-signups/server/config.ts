import { isValidTimeZone } from "../shared/periods.ts";
import type { WeekStart } from "../shared/stats.ts";

export type Env = Record<string, string | undefined>;

export interface DataConfig {
  dataMode: "demo" | "live";
  timeZone: string;
  weekStart: WeekStart;
}

export type AuthConfig =
  | { kind: "enabled"; passphraseHash: string; sessionSecret: string; sessionDays: number }
  | { kind: "disabled" }
  | { kind: "invalid"; problems: string[] };

const BCRYPT = /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/;

function clean(value: string | undefined): string {
  return (value ?? "").trim();
}

/**
 * Data settings. Unset DATA_MODE means demo. Anything unrecognized is an error: a typo must
 * never quietly land on the wrong data source.
 */
export function loadDataConfig(env: Env): { ok: true; config: DataConfig } | { ok: false; problems: string[] } {
  const problems: string[] = [];
  const modeRaw = clean(env.DATA_MODE).toLowerCase();
  let dataMode: DataConfig["dataMode"] = "demo";
  if (modeRaw === "live" || modeRaw === "demo") dataMode = modeRaw;
  else if (modeRaw !== "") problems.push("DATA_MODE must be 'demo' or 'live'");

  const timeZone = clean(env.DASHBOARD_TIMEZONE) || "America/New_York";
  if (!isValidTimeZone(timeZone)) problems.push("DASHBOARD_TIMEZONE is not a valid IANA time zone");

  const weekRaw = clean(env.WEEK_START).toUpperCase() || "MONDAY";
  if (weekRaw !== "MONDAY" && weekRaw !== "SUNDAY") problems.push("WEEK_START must be MONDAY or SUNDAY");

  if (problems.length > 0) return { ok: false, problems };
  return { ok: true, config: { dataMode, timeZone, weekStart: weekRaw as WeekStart } };
}

/** Authentication settings. Partially set or malformed means invalid, which callers treat as fail-closed. */
export function loadAuthConfig(env: Env): AuthConfig {
  const hash = clean(env.DASHBOARD_PASSPHRASE_HASH);
  const secret = clean(env.SESSION_SECRET);
  const daysRaw = clean(env.SESSION_DAYS);
  if (hash === "" && secret === "") return { kind: "disabled" };

  const problems: string[] = [];
  if (hash === "") problems.push("DASHBOARD_PASSPHRASE_HASH is missing");
  else if (!BCRYPT.test(hash)) {
    problems.push("DASHBOARD_PASSPHRASE_HASH is not a bcrypt hash (a '$' may have been stripped by shell or .env expansion)");
  }
  if (secret === "") problems.push("SESSION_SECRET is missing");
  else if (secret.length < 32) problems.push("SESSION_SECRET must be at least 32 characters");

  let sessionDays = 30;
  if (daysRaw !== "") {
    const n = Number(daysRaw);
    if (!Number.isInteger(n) || n < 1 || n > 365) problems.push("SESSION_DAYS must be a whole number from 1 to 365");
    else sessionDays = n;
  }
  if (problems.length > 0) return { kind: "invalid", problems };
  return { kind: "enabled", passphraseHash: hash, sessionSecret: secret, sessionDays };
}

/** Live mode always requires authentication. Demo mode requires it only once it is configured. */
export function authRequired(dataMode: DataConfig["dataMode"], auth: AuthConfig): boolean {
  return dataMode === "live" || auth.kind !== "disabled";
}

/**
 * Store namespace. Site-wide Blobs stores are shared by every deploy, so the name carries the
 * mode and (outside production) the deploy context. A preview can never read or write the
 * production live snapshot, and demo data never mixes with live data.
 */
export function snapshotStoreName(mode: DataConfig["dataMode"], deployContext: string | undefined): string {
  const ctx = (deployContext ?? "local").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "local";
  return ctx === "production" ? `mh-signups-${mode}` : `mh-signups-${mode}-${ctx}`;
}
