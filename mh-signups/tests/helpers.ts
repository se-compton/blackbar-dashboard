import bcrypt from "bcryptjs";
import type { AdapterErrorCode, FetchWindow, SignupSource, SourceResult } from "../server/adapter.ts";
import { AdapterError } from "../server/adapter.ts";
import type { DataConfig, Env } from "../server/config.ts";
import { memoryKv, type KeyValueStore } from "../server/kv.ts";
import type { Logger } from "../server/log.ts";
import type { Runtime } from "../server/runtime.ts";
import { SnapshotStore } from "../server/snapshot-store.ts";

export const PASSPHRASE = "correct horse battery staple";
export const PASSPHRASE_HASH = bcrypt.hashSync(PASSPHRASE, 4); // low cost: tests only
export const SESSION_SECRET = "test-secret-".padEnd(48, "x");
export const AUTH_ENV: Env = { DASHBOARD_PASSPHRASE_HASH: PASSPHRASE_HASH, SESSION_SECRET };

export const ORIGIN = "https://mh-signups.example.test";

export function runtime(opts: { env?: Env; now?: () => Date; log?: Logger } = {}): Runtime {
  return {
    env: opts.env ?? {},
    now: opts.now ?? (() => new Date()),
    openKv: (name: string) => memoryKv(name),
    sleep: async () => {},
    log: opts.log ?? { info() {}, warn() {}, error() {} },
  };
}

export function capturingLogger(): Logger & { lines: string[] } {
  const lines: string[] = [];
  const push = (level: string) => (event: string, fields?: Record<string, unknown>) =>
    void lines.push(JSON.stringify({ level, event, ...fields }));
  return { lines, info: push("info"), warn: push("warn"), error: push("error") };
}

export const DEMO_CONFIG: DataConfig = { dataMode: "demo", timeZone: "America/New_York", weekStart: "MONDAY" };
export const LIVE_CONFIG: DataConfig = { dataMode: "live", timeZone: "America/New_York", weekStart: "MONDAY" };

export function stubSource(
  fn: (w: FetchWindow, call: number) => Promise<SourceResult> | SourceResult,
  opts: { kind?: "demo" | "leaddocket"; configured?: boolean } = {},
): SignupSource & { calls: number } {
  const source: SignupSource & { calls: number } = {
    kind: opts.kind ?? "leaddocket",
    calls: 0,
    isConfigured: () => opts.configured ?? true,
    async fetchSignups(w: FetchWindow): Promise<SourceResult> {
      source.calls += 1;
      return fn(w, source.calls);
    },
  };
  return source;
}

export function failingSource(code: AdapterErrorCode, opts: { retryAfterMs?: number } = {}) {
  return stubSource(() => {
    throw new AdapterError(code, opts);
  });
}

export function store(name = "test-store"): { kv: KeyValueStore; snapshots: SnapshotStore } {
  const kv = memoryKv(name);
  return { kv, snapshots: new SnapshotStore(kv) };
}

export function request(path: string, init: RequestInit & { origin?: string | null } = {}): Request {
  const { origin = ORIGIN, headers, ...rest } = init;
  const h = new Headers(headers);
  if (origin) h.set("origin", origin);
  return new Request(`${ORIGIN}${path}`, { ...rest, headers: h });
}

export function loginRequest(passphrase: string, extra: RequestInit & { origin?: string | null } = {}): Request {
  return request("/api/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ passphrase }),
    ...extra,
  });
}

export function cookieFrom(res: Response): string {
  const raw = res.headers.get("set-cookie") ?? "";
  return raw.split(";")[0] ?? "";
}

export function signedAt(iso: string) {
  return iso;
}
