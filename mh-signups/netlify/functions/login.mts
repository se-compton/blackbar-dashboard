import type { Config, Context } from "@netlify/functions";
import { handleLogin } from "../../server/handlers.ts";
import { systemRuntime } from "../../server/runtime.ts";

export default async (req: Request, context: Context): Promise<Response> =>
  handleLogin(req, { ip: context.ip, deployContext: context.deploy?.context }, systemRuntime());

export const config: Config = { path: "/api/login", method: ["POST"] };
