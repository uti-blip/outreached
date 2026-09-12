import { createHash, createHmac, randomBytes, scrypt, timingSafeEqual } from "node:crypto";

export const SESSION_TTL_SECONDS = 8 * 60 * 60;
export type AuthConfig = { username: string; passwordHash: string; secret: string; origin: string | null; secure: boolean };
export type Session = { v: 1; iat: number; exp: number; nonce: string };
const passwordPattern = /^scrypt:16384:8:1:([a-f0-9]{32}):([a-f0-9]{128})$/;

export function authConfig(env: Record<string, string | undefined>): AuthConfig | null {
  const username = env.WORKSPACE_LOGIN_USER?.trim();
  const passwordHash = env.WORKSPACE_PASSWORD_HASH;
  const secret = env.WORKSPACE_SESSION_SECRET;
  const secure = env.NODE_ENV === "production";
  if (!username || username.length > 200 || !passwordHash || !passwordPattern.test(passwordHash) || !secret || secret.length < 32) return null;
  let origin: string | null = null;
  try {
    if (env.WORKSPACE_PUBLIC_ORIGIN) {
      const parsed = new URL(env.WORKSPACE_PUBLIC_ORIGIN);
      if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash || !["http:", "https:"].includes(parsed.protocol)) return null;
      if (secure && parsed.protocol !== "https:") return null;
      origin = parsed.origin;
    }
  } catch { return null; }
  if (secure && !origin) return null;
  return { username, passwordHash, secret, origin, secure };
}

function equal(left: string, right: string): boolean {
  return timingSafeEqual(createHash("sha256").update(left).digest(), createHash("sha256").update(right).digest());
}

export async function verifyPassword(username: unknown, password: unknown, config: AuthConfig): Promise<boolean> {
  if (typeof username !== "string" || typeof password !== "string" || username.length > 200 || password.length > 1024) return false;
  const match = passwordPattern.exec(config.passwordHash);
  if (!match) return false;
  const actual = await new Promise<Buffer>((resolve, reject) => {
    scrypt(password, Buffer.from(match[1], "hex"), 64, { N: 16384, r: 8, p: 1 }, (error, key) => error ? reject(error) : resolve(key));
  });
  // Always derive the password, even for an unknown username.
  const passwordMatches = timingSafeEqual(actual, Buffer.from(match[2], "hex"));
  return equal(username, config.username) && passwordMatches;
}

function mac(value: string, config: AuthConfig): string {
  // Credential changes invalidate previously issued sessions.
  return createHmac("sha256", config.secret).update(JSON.stringify([config.username, config.passwordHash, value])).digest("base64url");
}

export function createSession(config: AuthConfig, now = Date.now()): string {
  const issued = Math.floor(now / 1000);
  const session: Session = { v: 1, iat: issued, exp: issued + SESSION_TTL_SECONDS, nonce: randomBytes(24).toString("base64url") };
  const payload = Buffer.from(JSON.stringify(session)).toString("base64url");
  return `${payload}.${mac(`session.${payload}`, config)}`;
}

export function verifySession(token: string | undefined, config: AuthConfig, now = Date.now()): Session | null {
  if (!token || token.length > 1024) return null;
  const parts = token.split(".");
  if (parts.length !== 2 || !/^[\w-]+$/.test(parts[0]) || !/^[\w-]{43}$/.test(parts[1]) || !equal(parts[1], mac(`session.${parts[0]}`, config))) return null;
  try {
    const value: Session = JSON.parse(Buffer.from(parts[0], "base64url").toString());
    const seconds = Math.floor(now / 1000);
    if (value.v !== 1 || !Number.isSafeInteger(value.iat) || !Number.isSafeInteger(value.exp) || value.iat > seconds + 60 || value.exp <= seconds || value.exp - value.iat !== SESSION_TTL_SECONDS || typeof value.nonce !== "string" || !/^[\w-]{32}$/.test(value.nonce)) return null;
    return value;
  } catch { return null; }
}

export function csrfToken(token: string, config: AuthConfig): string { return mac(`csrf.${token}`, config); }
export function verifyCsrf(token: string, value: string | null, config: AuthConfig): boolean {
  return Boolean(value && /^[\w-]{43}$/.test(value) && equal(value, csrfToken(token, config)));
}
export function sessionCookieName(config: AuthConfig): string { return config.secure ? "__Host-workspace-session" : "workspace-session"; }

export function allowedOrigin(request: Request, config: AuthConfig, requireOrigin = false): boolean {
  try {
    const requestUrl = new URL(request.url);
    const expected = config.origin ?? requestUrl.origin;
    if (!config.origin && !["localhost", "127.0.0.1", "[::1]"].includes(requestUrl.hostname)) return false;
    if (request.headers.get("host") !== new URL(expected).host) return false;
    const site = request.headers.get("sec-fetch-site");
    if (site && !["same-origin", "none"].includes(site)) return false;
    const origin = request.headers.get("origin");
    return origin ? origin === expected : !requireOrigin;
  } catch { return false; }
}

export class LoginLimiter {
  private attempts = new Map<string, { count: number; reset: number }>();
  private limit: number;
  private windowMs: number;
  constructor(limit = 8, windowMs = 15 * 60 * 1000) { this.limit = limit; this.windowMs = windowMs; }
  take(key: string, now = Date.now()): boolean {
    for (const [id, value] of this.attempts) if (value.reset <= now) this.attempts.delete(id);
    const previous = this.attempts.get(key);
    if (previous) { previous.count += 1; return previous.count <= this.limit; }
    if (this.attempts.size >= 500) return false;
    this.attempts.set(key, { count: 1, reset: now + this.windowMs });
    return true;
  }
}

export function allowLoginAttempt(address: string, perAddress: LoginLimiter, total: LoginLimiter, now = Date.now()): boolean {
  // A source that has already been throttled must not exhaust the shared budget.
  return perAddress.take(address, now) && total.take("login", now);
}
