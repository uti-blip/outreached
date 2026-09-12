import { createHmac } from "node:crypto";
import { isIP } from "node:net";

export const LOGIN_WINDOW_MS = 15 * 60 * 1000;
export const LOGIN_GLOBAL_LIMIT = 60;
export const LOGIN_CLIENT_LIMIT = 8;
export const LOGIN_MAX_CLIENTS = 500;

type Bucket = { count: number; reset: number };
export type LoginRateLimitState = { v: 1; global: Bucket; clients: Record<string, Bucket> };

/** Only Netlify's connection header is trusted, and only on a Netlify runtime. */
export function loginClientKey(request: Request, secret: string, env: Record<string, string | undefined>): string | null {
  if (env.NETLIFY !== "true") return null;
  const address = request.headers.get("x-nf-client-connection-ip")?.trim();
  if (!address || address.length > 45 || address.includes("%") || !isIP(address)) return null;
  const canonical = isIP(address) === 6 ? new URL(`http://[${address}]/`).hostname : address;
  // Domain separation prevents reuse as a session MAC; raw addresses are never persisted.
  return createHmac("sha256", secret).update(`login-source:${canonical}`).digest("hex");
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function bucket(value: unknown, limit: number): value is Bucket {
  return object(value) && Object.keys(value).length === 2
    && Number.isSafeInteger(value.count) && Number(value.count) >= 0 && Number(value.count) <= limit
    && Number.isSafeInteger(value.reset) && Number(value.reset) >= 0;
}

function decodeState(value: unknown, now: number): LoginRateLimitState {
  if (object(value) && Object.keys(value).length === 0) return { v: 1, global: { count: 0, reset: now + LOGIN_WINDOW_MS }, clients: {} };
  if (!object(value) || value.v !== 1 || Object.keys(value).length !== 3 || !bucket(value.global, LOGIN_GLOBAL_LIMIT) || !object(value.clients)) throw new Error("Login rate limit state is unavailable.");
  const clients = Object.entries(value.clients);
  if (clients.length > LOGIN_MAX_CLIENTS || clients.some(([key, entry]) => !/^[a-f0-9]{64}$/.test(key) || !bucket(entry, LOGIN_CLIENT_LIMIT))) throw new Error("Login rate limit state is unavailable.");
  return {
    v: 1,
    global: value.global.reset <= now ? { count: 0, reset: now + LOGIN_WINDOW_MS } : { ...value.global },
    clients: Object.fromEntries(clients.filter(([, entry]) => (entry as Bucket).reset > now).map(([key, entry]) => [key, { ...(entry as Bucket) }])),
  };
}

/** Apply while holding the shared database lock; there is no per-process state. */
export function nextLoginRateLimit(value: unknown, clientKey: string | null, now = Date.now()): { allowed: boolean; state: LoginRateLimitState } {
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - LOGIN_WINDOW_MS) throw new Error("Invalid login rate limit clock.");
  if (clientKey !== null && !/^[a-f0-9]{64}$/.test(clientKey)) throw new Error("Invalid login rate limit source.");
  const state = decodeState(value, now);
  const client = clientKey === null ? null : state.clients[clientKey];
  if (client && client.count >= LOGIN_CLIENT_LIMIT) return { allowed: false, state };
  if (state.global.count >= LOGIN_GLOBAL_LIMIT) return { allowed: false, state };
  if (clientKey !== null && !client && Object.keys(state.clients).length >= LOGIN_MAX_CLIENTS) return { allowed: false, state };
  state.global.count += 1;
  if (clientKey !== null) state.clients[clientKey] = client ? { ...client, count: client.count + 1 } : { count: 1, reset: now + LOGIN_WINDOW_MS };
  return { allowed: true, state };
}
