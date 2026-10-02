import type { Config, Context } from "@netlify/functions";
import { handleStats } from "../../server/handlers.ts";
import { systemRuntime } from "../../server/runtime.ts";

export default async (req: Request, context: Context): Promise<Response> =>
  handleStats(req, { ip: context.ip, deployContext: context.deploy?.context }, systemRuntime());

export const config: Config = { path: "/api/stats", method: ["GET"] };
