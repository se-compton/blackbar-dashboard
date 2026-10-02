import type { SignupSource } from "./adapter.ts";
import type { DataConfig, Env } from "./config.ts";
import { createDemoSource } from "./demo-source.ts";
import { createLeadDocketSource } from "./lead-docket.ts";

/**
 * Explicit source selection. Live mode always uses Lead Docket. If Lead Docket is not
 * configured the sync fails visibly. It never falls back to demo data.
 */
export function selectSource(config: DataConfig, env: Env, now: () => Date): SignupSource {
  return config.dataMode === "live" ? createLeadDocketSource(env) : createDemoSource(now);
}
