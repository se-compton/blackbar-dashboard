/**
 * Structured logging with a hard allow-list on value types and a deny-list on key names.
 * Callers pass codes, counts, and durations. Source payloads, identifiers, and secrets must
 * never be passed; this is a backstop, not a license.
 */
export interface Logger {
  info(event: string, fields?: Record<string, unknown>): void;
  warn(event: string, fields?: Record<string, unknown>): void;
  error(event: string, fields?: Record<string, unknown>): void;
}

const SENSITIVE_KEY = /secret|key|pass|token|auth|cookie|session|hash|body|payload|lead|name|phone|email|^ip$|clientip/i;

export function sanitizeFields(fields: Record<string, unknown> = {}): Record<string, string | number | boolean | null> {
  const out: Record<string, string | number | boolean | null> = {};
  for (const [k, v] of Object.entries(fields)) {
    if (SENSITIVE_KEY.test(k)) {
      out[k] = "[redacted]";
    } else if (v === null || typeof v === "number" || typeof v === "boolean") {
      out[k] = v;
    } else if (typeof v === "string") {
      out[k] = v.length > 120 ? `${v.slice(0, 120)}...` : v;
    } else {
      out[k] = "[omitted]";
    }
  }
  return out;
}

export const consoleLogger: Logger = {
  info: (event, fields) => console.log(JSON.stringify({ level: "info", app: "mh-signups", event, ...sanitizeFields(fields) })),
  warn: (event, fields) => console.warn(JSON.stringify({ level: "warn", app: "mh-signups", event, ...sanitizeFields(fields) })),
  error: (event, fields) => console.error(JSON.stringify({ level: "error", app: "mh-signups", event, ...sanitizeFields(fields) })),
};

export const silentLogger: Logger = { info() {}, warn() {}, error() {} };
