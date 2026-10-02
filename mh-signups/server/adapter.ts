/**
 * Internal adapter contract between the app and any signup source.
 *
 * The rest of the app depends only on this file, never on Lead Docket's raw response shape.
 * An adapter returns EITHER normalized signup records (the app counts them) OR verified
 * period aggregates (the source counted them). Which one is right depends on what the
 * tenant's real API supports. Rex decides; see REX_HANDOFF.md.
 */

export type AdapterErrorCode =
  | "not_configured" // required settings are missing
  | "integration_incomplete" // settings present, but the adapter is not implemented yet
  | "unauthorized" // 401/403 from the source
  | "rate_limited" // 429 from the source
  | "timeout" // request or overall deadline exceeded
  | "upstream" // 5xx or network failure
  | "malformed" // response did not match what the adapter expects
  | "incomplete"; // pagination could not be completed; counts would be partial

const MESSAGES: Record<AdapterErrorCode, string> = {
  not_configured: "Source is not configured",
  integration_incomplete: "Source integration is not implemented",
  unauthorized: "Source rejected the credentials",
  rate_limited: "Source rate limit reached",
  timeout: "Source request timed out",
  upstream: "Source request failed",
  malformed: "Source response was not in the expected shape",
  incomplete: "Source results could not be fully retrieved",
};

/** Error with a fixed, safe message. Never put response bodies, URLs with keys, or client data in here. */
export class AdapterError extends Error {
  readonly code: AdapterErrorCode;
  readonly status?: number;
  readonly retryAfterMs?: number;

  constructor(code: AdapterErrorCode, opts: { status?: number; retryAfterMs?: number } = {}) {
    super(MESSAGES[code]);
    this.name = "AdapterError";
    this.code = code;
    if (opts.status !== undefined) this.status = opts.status;
    if (opts.retryAfterMs !== undefined) this.retryAfterMs = opts.retryAfterMs;
  }
}

/**
 * One distinct lead/case that qualifies as a signup.
 * leadId: the source system's stable lead/case identifier (never a name, phone, or signature count).
 * signedAt: ISO timestamp WITH offset or Z for the authoritative signup event of that lead. If the
 *   lead has several signing events, return the FIRST qualifying one. Never a created/modified/export date.
 */
export interface SignupRecord {
  leadId: string;
  signedAt: string;
}

export interface FetchWindow {
  /** Inclusive start: the earlier of the week start and the month start. */
  from: Date;
  /** Exclusive end: the common reporting cutoff. */
  to: Date;
  /** Abort when the overall refresh deadline passes. Pass it to every outbound request. */
  signal: AbortSignal;
}

export type SourceResult =
  | { type: "records"; records: SignupRecord[] }
  | { type: "aggregates"; today: number; week: number; month: number };

export interface SignupSource {
  readonly kind: "demo" | "leaddocket";
  /** True when this source has what it needs to attempt a sync. */
  isConfigured(): boolean;
  /**
   * Must return the COMPLETE result for the window or throw an AdapterError. Never return a
   * partial page set. READ-ONLY: no creating, updating, or annotating anything in the source.
   */
  fetchSignups(window: FetchWindow): Promise<SourceResult>;
}

/** Map an HTTP status to an error code. Generic HTTP semantics only. */
export function codeForHttpStatus(status: number): AdapterErrorCode {
  if (status === 401 || status === 403) return "unauthorized";
  if (status === 429) return "rate_limited";
  return "upstream";
}

/** Parse a Retry-After header (delta seconds or HTTP date) into ms, or undefined. */
export function parseRetryAfter(header: string | null, now: Date = new Date()): number | undefined {
  if (!header) return undefined;
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.round(seconds * 1000);
  const at = Date.parse(header);
  if (Number.isNaN(at)) return undefined;
  return Math.max(0, at - now.getTime());
}
