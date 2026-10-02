import { applyPeriodGuard, effectiveStatus } from "../shared/status.ts";
import type { MetricKey, StatsResponse } from "../shared/stats.ts";

export type Phase = "loading" | "ready" | "login" | "auth_unconfigured";

export interface ViewInput {
  phase: Phase;
  stats: StatsResponse | null;
  /** True when the last attempt to read the saved snapshot failed. */
  offline: boolean;
  now: Date;
}

export interface MetricView {
  key: MetricKey;
  label: string;
  /** Formatted number, or "--" when unavailable. Zero is shown as "0" only when it is a real zero. */
  value: string;
  /** Short explanation shown under an unavailable number. */
  note: string | null;
}

export interface ScreenView {
  demo: boolean;
  metrics: MetricView[];
  /** Status lines such as "Update delayed". Empty when all is well. */
  banners: string[];
  footer: string;
}

export const LABELS: Record<MetricKey, string> = {
  today: "SIGNED TODAY",
  week: "SIGNED THIS WEEK",
  month: "SIGNED THIS MONTH",
};

export function formatUpdated(iso: string, timeZone: string): string {
  const text = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(iso));
  return `Updated ${text}`;
}

export function buildView({ phase, stats, offline, now }: ViewInput): ScreenView {
  const keys: MetricKey[] = ["today", "week", "month"];
  const banners: string[] = [];

  if (stats === null) {
    const note = phase === "loading" ? "Loading" : offline ? "Can't reach the server" : null;
    if (offline && phase !== "loading") banners.push("Offline. Numbers are not current.");
    return {
      demo: false,
      metrics: keys.map((key) => ({ key, label: LABELS[key], value: "--", note })),
      banners,
      footer: "No update yet",
    };
  }

  // Re-check the calendar every render so a rollover never shows last period's number.
  const guarded = applyPeriodGuard(stats, now);
  const status = effectiveStatus(guarded, now);

  const metrics = keys.map((key): MetricView => {
    const v = guarded[key];
    if (v !== null) return { key, label: LABELS[key], value: v.toLocaleString("en-US"), note: null };
    const note =
      guarded.unavailableReason === "not_configured"
        ? "Connection not configured"
        : guarded.unavailableReason === "waiting_first_update"
          ? "Waiting for first update"
          : "Waiting for update";
    return { key, label: LABELS[key], value: "--", note };
  });

  if (offline) banners.push("Offline. Numbers are not current.");
  if (status === "delayed") banners.push("Update delayed");

  return {
    demo: guarded.source === "demo",
    metrics,
    banners,
    footer: guarded.lastSuccessfulSyncAt ? formatUpdated(guarded.lastSuccessfulSyncAt, guarded.timeZone) : "No update yet",
  };
}
