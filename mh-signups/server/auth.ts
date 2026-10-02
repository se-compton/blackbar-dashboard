import bcrypt from "bcryptjs";
import { SignJWT, jwtVerify } from "jose";
import { createHmac } from "node:crypto";
import type { AuthConfig } from "./config.ts";
import { parseCookies, sessionCookieName } from "./http.ts";
import type { KeyValueStore } from "./kv.ts";

const ISSUER = "mh-signups";
const AUDIENCE = "mh-signups-viewer";
const MAX_PASSPHRASE_BYTES = 72; // bcrypt ignores anything beyond this

type Enabled = Extract<AuthConfig, { kind: "enabled" }>;

export async function verifyPassphrase(candidate: string, hash: string): Promise<boolean> {
  if (candidate === "" || Buffer.byteLength(candidate, "utf8") > MAX_PASSPHRASE_BYTES) return false;
  return bcrypt.compare(candidate, hash);
}

/** Signed, expiring session. Rotating SESSION_SECRET invalidates every outstanding session. */
export async function createSessionToken(auth: Enabled, now: Date): Promise<{ token: string; maxAgeSeconds: number }> {
  const maxAgeSeconds = auth.sessionDays * 86_400;
  const iat = Math.floor(now.getTime() / 1000);
  const token = await new SignJWT({})
    .setProtectedHeader({ alg: "HS256" })
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setSubject("viewer")
    .setIssuedAt(iat)
    .setExpirationTime(iat + maxAgeSeconds)
    .sign(new TextEncoder().encode(auth.sessionSecret));
  return { token, maxAgeSeconds };
}

export async function hasValidSession(req: Request, auth: Enabled, now: Date): Promise<boolean> {
  const token = parseCookies(req.headers.get("cookie")).get(sessionCookieName(req));
  if (!token) return false;
  try {
    await jwtVerify(token, new TextEncoder().encode(auth.sessionSecret), {
      algorithms: ["HS256"],
      issuer: ISSUER,
      audience: AUDIENCE,
      currentDate: now,
    });
    return true;
  } catch {
    return false;
  }
}

// ---- Failed-login throttling, shared across serverless instances through the KV store ----

const MAX_FAILURES = 5;
const WINDOW_MS = 15 * 60_000;
const LOCK_MS = 15 * 60_000;

interface ThrottleRecord {
  failures: number;
  windowStart: number;
  lockedUntil: number;
}

function throttleKey(ip: string | undefined, secret: string): string {
  const id = createHmac("sha256", secret).update(ip ?? "unknown").digest("hex").slice(0, 32);
  return `login-throttle/${id}`;
}

async function readThrottle(kv: KeyValueStore, key: string): Promise<ThrottleRecord> {
  const hit = await kv.get(key);
  const v = hit?.value as Partial<ThrottleRecord> | undefined;
  return {
    failures: typeof v?.failures === "number" ? v.failures : 0,
    windowStart: typeof v?.windowStart === "number" ? v.windowStart : 0,
    lockedUntil: typeof v?.lockedUntil === "number" ? v.lockedUntil : 0,
  };
}

/** Returns seconds to wait, or 0 if the client may try. */
export async function throttleRetryAfter(kv: KeyValueStore, ip: string | undefined, secret: string, now: Date): Promise<number> {
  const rec = await readThrottle(kv, throttleKey(ip, secret));
  return rec.lockedUntil > now.getTime() ? Math.ceil((rec.lockedUntil - now.getTime()) / 1000) : 0;
}

export async function recordFailedLogin(kv: KeyValueStore, ip: string | undefined, secret: string, now: Date): Promise<void> {
  const key = throttleKey(ip, secret);
  const rec = await readThrottle(kv, key);
  const t = now.getTime();
  const fresh = t - rec.windowStart > WINDOW_MS;
  const failures = (fresh ? 0 : rec.failures) + 1;
  await kv.put(key, {
    failures,
    windowStart: fresh ? t : rec.windowStart,
    lockedUntil: failures >= MAX_FAILURES ? t + LOCK_MS : 0,
  });
}

export async function clearFailedLogins(kv: KeyValueStore, ip: string | undefined, secret: string): Promise<void> {
  await kv.put(throttleKey(ip, secret), { failures: 0, windowStart: 0, lockedUntil: 0 });
}
