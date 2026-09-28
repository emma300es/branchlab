import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const SESSION_SECONDS = 8 * 60 * 60;
export type AuthConfig = { remote: boolean; origin: string | null; ownerToken: string; sessionSecret: string; apiToken: string; cookieName: string; secure: boolean };
const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]);
const localHost = /^(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$/;

/** A configured origin explicitly opts into single-owner authenticated deployment. */
export function authConfig(env: Readonly<Record<string, string | undefined>> = process.env): AuthConfig {
  const value = env.BRANCHLAB_PUBLIC_ORIGIN;
  const ownerToken = env.BRANCHLAB_OWNER_TOKEN ?? "";
  const sessionSecret = env.BRANCHLAB_SESSION_SECRET ?? "";
  const apiToken = env.BRANCHLAB_API_TOKEN ?? "";
  if (!value) {
    if (ownerToken || sessionSecret) throw new Error("Authentication requires an explicit public origin.");
    return { remote: false, origin: null, ownerToken: "", sessionSecret: "", apiToken, cookieName: "branchlab-session", secure: false };
  }
  const origin = new URL(value);
  if (origin.origin !== value || origin.username || origin.password || origin.search || origin.hash ||
      (origin.protocol !== "https:" && !(origin.protocol === "http:" && loopback.has(origin.hostname)))) {
    throw new Error("Public origin must be an exact HTTPS origin (HTTP is only allowed on loopback).");
  }
  if ([ownerToken, sessionSecret, apiToken].some(secret => secret.length < 32 || secret.length > 4096)) throw new Error("Remote mode requires three configured secrets of at least 32 characters.");
  if (new Set([ownerToken, sessionSecret, apiToken]).size !== 3) throw new Error("Use distinct owner, session, and backend secrets.");
  return { remote: true, origin: value, ownerToken, sessionSecret, apiToken, cookieName: origin.protocol === "https:" ? "__Host-branchlab-session" : "branchlab-session", secure: origin.protocol === "https:" };
}

export function requestOrigin(url: string, host: string | null, config: AuthConfig): string | null {
  if (!host) return null;
  if (config.remote) return host === new URL(config.origin!).host ? config.origin : null;
  if (!localHost.test(host)) return null;
  const parsed = new URL(url);
  return `${parsed.protocol}//${host}`;
}

export function sameOriginJson(request: Request, config: AuthConfig): boolean {
  const origin = requestOrigin(request.url, request.headers.get("host"), config);
  const site = request.headers.get("sec-fetch-site");
  return origin !== null && request.headers.get("origin") === origin && (!site || site === "same-origin") && request.headers.get("content-type")?.split(";")[0].trim() === "application/json";
}

export function tokenMatches(candidate: string, expected: string): boolean {
  // Fixed-length digest comparison avoids leaking a correct prefix or token length.
  return timingSafeEqual(createHash("sha256").update(candidate).digest(), createHash("sha256").update(expected).digest());
}

export function createSession(config: AuthConfig, now = Date.now()): string {
  const body = Buffer.from(JSON.stringify({ v: 1, aud: config.origin, exp: Math.floor(now / 1000) + SESSION_SECONDS, nonce: randomBytes(16).toString("hex") })).toString("base64url");
  return `${body}.${createHmac("sha256", config.sessionSecret).update(body).digest("base64url")}`;
}

export function validSession(value: string | undefined, config: AuthConfig, now = Date.now()): boolean {
  if (!value || value.length > 2048 || !config.remote) return false;
  const parts = value.split(".");
  if (parts.length !== 2 || !/^[A-Za-z0-9_-]+$/.test(parts[0]) || !/^[A-Za-z0-9_-]{43}$/.test(parts[1])) return false;
  const expected = createHmac("sha256", config.sessionSecret).update(parts[0]).digest("base64url");
  if (!tokenMatches(parts[1], expected)) return false;
  try {
    const data = JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8"));
    const seconds = Math.floor(now / 1000);
    return data.v === 1 && data.aud === config.origin && Number.isInteger(data.exp) && data.exp > seconds && data.exp <= seconds + SESSION_SECONDS && /^[a-f0-9]{32}$/.test(data.nonce);
  } catch { return false; }
}

/** Global rather than client-supplied-IP keyed: forwarding headers cannot bypass it. */
export class LoginLimiter {
  private attempts: number[] = [];
  allow(now = Date.now()): boolean {
    this.attempts = this.attempts.filter(time => time > now - 60_000);
    if (this.attempts.length >= 5) return false;
    this.attempts.push(now);
    return true;
  }
}
export const loginLimiter = new LoginLimiter();
