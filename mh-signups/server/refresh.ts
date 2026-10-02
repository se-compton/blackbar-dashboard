import { AdapterError, type SignupSource, type SourceResult } from "./adapter.ts";
import type { DataConfig } from "./config.ts";
import { resolveCounts } from "./count.ts";
import type { Logger } from "./log.ts";
import { SnapshotStore, type Snapshot, type WriteOutcome } from "./snapshot-store.ts";
import { computePeriodStarts, periodStartsIso, retrievalStart } from "../shared/periods.ts";

export interface RefreshDeps {
  config: DataConfig;
  source: SignupSource;
  snapshots: SnapshotStore;
  now: () => Date;
  sleep: (ms: number) => Promise<void>;
  log: Logger;
  /** Total time budget for one refresh. Netlify documents 30s for Scheduled Functions; stay under it. */
  deadlineMs?: number;
  maxAttempts?: number;
  /** Ignore a refresh that starts within this long after the previous attempt (guards stray direct hits). */
  minAttemptSpacingMs?: number;
}

export type RefreshOutcome =
  | { status: "success"; write: WriteOutcome }
  | { status: "failed"; code: string }
  | { status: "skipped_recent" };

const RETRYABLE = new Set(["timeout", "rate_limited", "upstream"]);
const MAX_RETRY_AFTER_MS = 10_000;

function normalize(err: unknown, deadlineHit: boolean): AdapterError {
  if (deadlineHit) return new AdapterError("timeout");
  if (err instanceof AdapterError) return err;
  return new AdapterError("upstream"); // unknown failure; the log carries only the error class name
}

/**
 * One complete sync. Writes all three totals and their metadata together, and only after a
 * complete, validated retrieval. Every failure path leaves the last good snapshot untouched.
 */
export async function runRefresh(deps: RefreshDeps): Promise<RefreshOutcome> {
  const { config, source, snapshots, log } = deps;
  const startedAt = deps.now();
  const state = await snapshots.readSyncState();

  const spacing = deps.minAttemptSpacingMs ?? 60_000;
  if (state.lastAttemptAt !== null && startedAt.getTime() - Date.parse(state.lastAttemptAt) < spacing) {
    log.info("refresh_skipped_recent");
    return { status: "skipped_recent" };
  }
  await snapshots.writeSyncState({ ...state, lastAttemptAt: startedAt.toISOString() });

  const fail = async (code: string, detail: Record<string, unknown> = {}): Promise<RefreshOutcome> => {
    const at = deps.now().toISOString();
    await snapshots.writeSyncState({
      lastAttemptAt: startedAt.toISOString(),
      lastFailureAt: at,
      lastFailureCode: code,
      consecutiveFailures: state.consecutiveFailures + 1,
    });
    log.error("refresh_failed", { code, source: source.kind, ...detail });
    return { status: "failed", code };
  };

  if (!source.isConfigured()) return fail("not_configured");

  // One cutoff for all three totals. Intervals are [periodStart, asOf).
  const asOf = startedAt;
  const starts = computePeriodStarts(asOf, config.timeZone, config.weekStart);
  const from = retrievalStart(starts);

  const controller = new AbortController();
  const deadlineMs = deps.deadlineMs ?? 25_000;
  const t0 = performance.now();
  const timer = setTimeout(() => controller.abort(), deadlineMs);
  const deadline = new Promise<never>((_, reject) => {
    controller.signal.addEventListener("abort", () => reject(new AdapterError("timeout")));
  });
  deadline.catch(() => {});

  let result: SourceResult | null = null;
  let lastError: AdapterError | null = null;
  const maxAttempts = deps.maxAttempts ?? 3;
  try {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        result = await Promise.race([source.fetchSignups({ from, to: asOf, signal: controller.signal }), deadline]);
        lastError = null;
        break;
      } catch (err) {
        lastError = normalize(err, controller.signal.aborted);
        log.warn("refresh_attempt_failed", {
          code: lastError.code,
          attempt,
          errorClass: err instanceof Error ? err.name : "unknown",
          status: lastError.status ?? null,
        });
        if (!RETRYABLE.has(lastError.code) || controller.signal.aborted || attempt === maxAttempts) break;
        const wait = lastError.retryAfterMs ?? 1000 * 2 ** (attempt - 1);
        if (wait > MAX_RETRY_AFTER_MS || performance.now() - t0 + wait >= deadlineMs) break;
        await deps.sleep(wait);
      }
    }
  } finally {
    clearTimeout(timer);
  }

  if (result === null) return fail(lastError?.code ?? "upstream");

  let counts;
  try {
    counts = resolveCounts(result, starts, asOf);
  } catch (err) {
    return fail(err instanceof AdapterError ? err.code : "malformed");
  }

  const completedAt = deps.now();
  const snapshot: Snapshot = {
    schemaVersion: 1,
    source: source.kind,
    timeZone: config.timeZone,
    weekStartsOn: config.weekStart,
    counts,
    periodStarts: periodStartsIso(asOf, config.timeZone, config.weekStart),
    asOf: asOf.toISOString(),
    lastSuccessfulSyncAt: completedAt.toISOString(),
  };
  const write = await snapshots.writeIfNewer(snapshot);
  await snapshots.writeSyncState({
    lastAttemptAt: startedAt.toISOString(),
    lastFailureAt: null,
    lastFailureCode: null,
    consecutiveFailures: 0,
  });
  log.info("refresh_succeeded", { write, source: source.kind, durationMs: Math.round(performance.now() - t0) });
  return { status: "success", write };
}
