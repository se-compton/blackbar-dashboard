import { isIsoTimestamp, type PeriodStarts, type StatsSource, type WeekStart } from "../shared/stats.ts";
import type { KeyValueStore } from "./kv.ts";
import type { Counts } from "./count.ts";

/** The only persisted business data: three totals plus the metadata needed to trust them. */
export interface Snapshot {
  schemaVersion: 1;
  source: StatsSource;
  timeZone: string;
  weekStartsOn: WeekStart;
  counts: Counts;
  periodStarts: PeriodStarts;
  asOf: string;
  lastSuccessfulSyncAt: string;
}

/** Operational metadata only. Failure codes, never messages or payloads. */
export interface SyncState {
  lastAttemptAt: string | null;
  lastFailureAt: string | null;
  lastFailureCode: string | null;
  consecutiveFailures: number;
}

export const EMPTY_SYNC_STATE: SyncState = {
  lastAttemptAt: null,
  lastFailureAt: null,
  lastFailureCode: null,
  consecutiveFailures: 0,
};

const SNAPSHOT_KEY = "snapshot";
const SYNC_KEY = "sync-state";

function isCount(v: unknown): v is number {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
}

function parseSnapshot(raw: unknown): Snapshot | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, any>;
  const ok =
    o.schemaVersion === 1 &&
    (o.source === "demo" || o.source === "leaddocket") &&
    typeof o.timeZone === "string" &&
    (o.weekStartsOn === "MONDAY" || o.weekStartsOn === "SUNDAY") &&
    isCount(o.counts?.today) &&
    isCount(o.counts?.week) &&
    isCount(o.counts?.month) &&
    isIsoTimestamp(o.periodStarts?.today) &&
    isIsoTimestamp(o.periodStarts?.week) &&
    isIsoTimestamp(o.periodStarts?.month) &&
    isIsoTimestamp(o.asOf) &&
    isIsoTimestamp(o.lastSuccessfulSyncAt);
  return ok ? (o as Snapshot) : null;
}

export type WriteOutcome = "written" | "skipped_not_newer" | "skipped_conflict";

export class SnapshotStore {
  constructor(private readonly kv: KeyValueStore) {}

  async readSnapshot(): Promise<Snapshot | null> {
    const hit = await this.kv.get(SNAPSHOT_KEY);
    return hit ? parseSnapshot(hit.value) : null;
  }

  /**
   * Save all three totals and their metadata as one record, and only if this run's data cutoff is
   * newer than what is stored. An older overlapping run therefore cannot replace a newer snapshot,
   * even if it finishes last (conditional write on the stored version, retried on conflict).
   */
  async writeIfNewer(next: Snapshot): Promise<WriteOutcome> {
    for (let attempt = 0; attempt < 3; attempt++) {
      const hit = await this.kv.get(SNAPSHOT_KEY);
      const current = hit ? parseSnapshot(hit.value) : null;
      if (current && Date.parse(current.asOf) >= Date.parse(next.asOf)) return "skipped_not_newer";
      const written = await this.kv.put(SNAPSHOT_KEY, next, hit ? hit.etag : null);
      if (written) return "written";
    }
    return "skipped_conflict";
  }

  async readSyncState(): Promise<SyncState> {
    const hit = await this.kv.get(SYNC_KEY);
    const v = hit?.value as Partial<SyncState> | undefined;
    if (!v || typeof v !== "object") return { ...EMPTY_SYNC_STATE };
    return {
      lastAttemptAt: typeof v.lastAttemptAt === "string" ? v.lastAttemptAt : null,
      lastFailureAt: typeof v.lastFailureAt === "string" ? v.lastFailureAt : null,
      lastFailureCode: typeof v.lastFailureCode === "string" ? v.lastFailureCode : null,
      consecutiveFailures: typeof v.consecutiveFailures === "number" ? v.consecutiveFailures : 0,
    };
  }

  async writeSyncState(state: SyncState): Promise<void> {
    await this.kv.put(SYNC_KEY, state);
  }
}
