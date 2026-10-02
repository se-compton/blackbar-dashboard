import type { DataConfig } from "./config.ts";
import { SnapshotStore } from "./snapshot-store.ts";
import { applyPeriodGuard, STALE_AFTER_MS } from "../shared/status.ts";
import type { StatsResponse } from "../shared/stats.ts";

export interface StatsInput {
  config: DataConfig;
  snapshots: SnapshotStore;
  now: Date;
  /** Live mode only: whether the Lead Docket adapter has the settings it needs. */
  sourceConfigured: boolean;
}

/**
 * Compose the response from the SAVED snapshot. Never calls Lead Docket. Nothing here can make
 * old data look new: timestamps come from the snapshot, and any metric whose saved period no
 * longer matches the current calendar period is nulled rather than shown under the new period.
 */
export async function buildStats({ config, snapshots, now, sourceConfigured }: StatsInput): Promise<StatsResponse> {
  const source = config.dataMode === "live" ? "leaddocket" : "demo";
  const base = {
    timeZone: config.timeZone,
    weekStartsOn: config.weekStart,
    source,
  } as const;

  const [snapshot, sync] = await Promise.all([snapshots.readSnapshot(), snapshots.readSyncState()]);
  const usable =
    snapshot !== null &&
    snapshot.source === source &&
    snapshot.timeZone === config.timeZone &&
    snapshot.weekStartsOn === config.weekStart;

  if (!usable) {
    return {
      ...base,
      today: null,
      week: null,
      month: null,
      periodStarts: null,
      asOf: null,
      lastSuccessfulSyncAt: null,
      status: "unavailable",
      unavailableReason: config.dataMode === "live" && !sourceConfigured ? "not_configured" : "waiting_first_update",
    };
  }

  const failureIsNewer =
    sync.consecutiveFailures > 0 &&
    sync.lastFailureAt !== null &&
    Date.parse(sync.lastFailureAt) >= Date.parse(snapshot.lastSuccessfulSyncAt);
  const tooOld = now.getTime() - Date.parse(snapshot.asOf) > STALE_AFTER_MS;

  const response: StatsResponse = {
    ...base,
    today: snapshot.counts.today,
    week: snapshot.counts.week,
    month: snapshot.counts.month,
    periodStarts: snapshot.periodStarts,
    asOf: snapshot.asOf,
    lastSuccessfulSyncAt: snapshot.lastSuccessfulSyncAt,
    status: failureIsNewer || tooOld ? "delayed" : "ok",
    unavailableReason: null,
  };
  return applyPeriodGuard(response, now);
}
