import { fetchStats, login, logout, type FetchLike, type LoginOutcome } from "./api.ts";
import type { Phase } from "./view-model.ts";
import type { StatsResponse } from "../shared/stats.ts";

export interface AppState {
  phase: Phase;
  stats: StatsResponse | null;
  offline: boolean;
  refreshing: boolean;
  loginError: null | "invalid" | "throttled" | "unconfigured" | "network_error";
  retryAfterSeconds: number;
  /** Updated on every tick so views can re-check calendar rollover without a network call. */
  nowMs: number;
}

export interface Environment {
  fetch: FetchLike;
  now(): number;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
  isVisible(): boolean;
}

export const POLL_MS = 60_000; // re-read the saved snapshot about once a minute while visible
export const RENDER_TICK_MS = 15_000; // re-check rollover/staleness locally, no network
export const RESUME_DEBOUNCE_MS = 2_000;

/**
 * Reads the SAVED snapshot only. It never causes a Lead Docket sync, and it never edits
 * timestamps: "Updated" always comes from the server's lastSuccessfulSyncAt.
 */
export class Controller {
  state: AppState;
  private listeners = new Set<(s: AppState) => void>();
  private pollHandle: unknown = null;
  private tickHandle: unknown = null;
  private lastFetchStartedAt = -Infinity;
  private inFlight: Promise<void> | null = null;

  constructor(private readonly env: Environment) {
    this.state = {
      phase: "loading",
      stats: null,
      offline: false,
      refreshing: false,
      loginError: null,
      retryAfterSeconds: 0,
      nowMs: env.now(),
    };
  }

  subscribe(fn: (s: AppState) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private set(patch: Partial<AppState>): void {
    this.state = { ...this.state, ...patch, nowMs: this.env.now() };
    for (const fn of this.listeners) fn(this.state);
  }

  /** Launch: fetch immediately, then keep timers running only while visible. */
  start(): void {
    void this.load("launch");
    if (this.env.isVisible()) this.startTimers();
  }

  stop(): void {
    this.stopTimers();
  }

  /** Call on visibilitychange, pageshow, and focus. Repeated events are debounced. */
  onVisibilityChange(): void {
    if (this.env.isVisible()) {
      this.startTimers();
      void this.load("resume");
    } else {
      this.stopTimers();
    }
  }

  onOnline(): void {
    if (this.env.isVisible()) void this.load("resume");
  }

  /** The "Refresh view" button. Re-reads the saved snapshot; does not ask Lead Docket for anything. */
  async refreshView(): Promise<void> {
    await this.load("manual");
  }

  async submitLogin(passphrase: string): Promise<LoginOutcome> {
    const outcome = await login(this.env.fetch, passphrase);
    if (outcome.kind === "ok") {
      this.set({ loginError: null });
      await this.load("manual");
    } else if (outcome.kind === "throttled") {
      this.set({ loginError: "throttled", retryAfterSeconds: outcome.retryAfterSeconds });
    } else {
      this.set({ loginError: outcome.kind });
    }
    return outcome;
  }

  async signOut(): Promise<void> {
    await logout(this.env.fetch);
    // Drop any in-memory counts immediately; the next read will require login again.
    this.set({ stats: null, phase: "login", offline: false, loginError: null });
  }

  private startTimers(): void {
    if (this.pollHandle === null) this.pollHandle = this.env.setInterval(() => void this.load("poll"), POLL_MS);
    if (this.tickHandle === null) this.tickHandle = this.env.setInterval(() => this.set({}), RENDER_TICK_MS);
  }

  private stopTimers(): void {
    if (this.pollHandle !== null) this.env.clearInterval(this.pollHandle);
    if (this.tickHandle !== null) this.env.clearInterval(this.tickHandle);
    this.pollHandle = null;
    this.tickHandle = null;
  }

  private load(reason: "launch" | "resume" | "poll" | "manual"): Promise<void> {
    if (this.inFlight) return this.inFlight;
    if (reason === "resume" && this.env.now() - this.lastFetchStartedAt < RESUME_DEBOUNCE_MS) return Promise.resolve();
    this.lastFetchStartedAt = this.env.now();
    this.set({ refreshing: reason === "manual" });
    this.inFlight = this.run().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  private async run(): Promise<void> {
    const outcome = await fetchStats(this.env.fetch);
    switch (outcome.kind) {
      case "ok":
        this.set({ phase: "ready", stats: outcome.stats, offline: false, refreshing: false });
        break;
      case "unauthorized":
        this.set({ phase: "login", stats: null, offline: false, refreshing: false });
        break;
      case "auth_unconfigured":
        this.set({ phase: "auth_unconfigured", stats: null, offline: false, refreshing: false });
        break;
      default:
        // Network trouble or an unreadable response: keep any in-memory numbers, but mark them not current.
        this.set({ phase: this.state.phase === "loading" ? "ready" : this.state.phase, offline: true, refreshing: false });
    }
  }
}
