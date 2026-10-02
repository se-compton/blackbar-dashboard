import type { Env } from "./config.ts";
import { openKv, type KeyValueStore } from "./kv.ts";
import { consoleLogger, type Logger } from "./log.ts";

/** Everything a handler needs from the outside world, injectable for tests. */
export interface Runtime {
  env: Env;
  now(): Date;
  openKv(name: string): KeyValueStore;
  sleep(ms: number): Promise<void>;
  log: Logger;
}

export function systemRuntime(): Runtime {
  return {
    env: process.env,
    now: () => new Date(),
    openKv,
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    log: consoleLogger,
  };
}

export interface RequestInfo {
  /** Client IP as reported by the platform, if any. */
  ip?: string;
  /** Netlify deploy context: production, deploy-preview, branch-deploy, dev. */
  deployContext?: string;
}
