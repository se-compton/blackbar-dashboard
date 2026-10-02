import type { Config, Context } from "@netlify/functions";
import { handleScheduledRefresh } from "../../server/handlers.ts";
import { systemRuntime } from "../../server/runtime.ts";

// Scheduled Function: syncs Lead Docket into the saved snapshot every 30 minutes.
// Netlify schedules in UTC; business date math still uses DASHBOARD_TIMEZONE.
// It does not trigger a site build. Automatic runs happen on the published production deploy only.
export default async (_req: Request, context: Context): Promise<Response> =>
  handleScheduledRefresh({ deployContext: context.deploy?.context }, systemRuntime());

export const config: Config = { schedule: "*/30 * * * *" };
