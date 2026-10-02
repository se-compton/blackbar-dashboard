/** Small HTTP helpers shared by the function handlers. */

export const NO_STORE = "private, no-store";

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  const h = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": NO_STORE,
    Vary: "Cookie",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
  });
  for (const [k, v] of Object.entries(headers)) h.set(k, v);
  return new Response(JSON.stringify(body), { status, headers: h });
}

export function methodNotAllowed(allow: string): Response {
  return jsonResponse({ error: "method_not_allowed" }, 405, { Allow: allow });
}

export function parseCookies(header: string | null): Map<string, string> {
  const out = new Map<string, string>();
  if (!header) return out;
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    if (name !== "" && !out.has(name)) out.set(name, part.slice(i + 1).trim());
  }
  return out;
}

export function isHttps(req: Request): boolean {
  return new URL(req.url).protocol === "https:";
}

/** `__Host-` prefix on HTTPS pins the cookie to this exact host. Plain name for http://localhost dev. */
export function sessionCookieName(req: Request): string {
  return isHttps(req) ? "__Host-mh_session" : "mh_session";
}

export function sessionCookie(req: Request, token: string, maxAgeSeconds: number): string {
  const attrs = [`${sessionCookieName(req)}=${token}`, "Path=/", "HttpOnly", "SameSite=Strict", `Max-Age=${maxAgeSeconds}`];
  if (isHttps(req)) attrs.push("Secure");
  return attrs.join("; ");
}

export function clearedSessionCookie(req: Request): string {
  return sessionCookie(req, "", 0);
}

/**
 * CSRF guard for state-changing requests: the Origin header must match this request's origin.
 * Requests with no Origin, a foreign Origin, or Sec-Fetch-Site: cross-site are rejected.
 */
export function isSameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return false;
  if (req.headers.get("sec-fetch-site") === "cross-site") return false;
  try {
    return new URL(origin).origin === new URL(req.url).origin;
  } catch {
    return false;
  }
}
