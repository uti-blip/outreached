import assert from "node:assert/strict";
import { randomBytes, scryptSync } from "node:crypto";
import { test } from "node:test";
import { allowedOrigin, allowLoginAttempt, authConfig, createSession, csrfToken, LoginLimiter, sessionCookieName, SESSION_TTL_SECONDS, verifyCsrf, verifyPassword, verifySession } from "../src/lib/auth-core.ts";

const password = randomBytes(24).toString("base64url");
const salt = randomBytes(16);
const env = {
  NODE_ENV: "production", WORKSPACE_LOGIN_USER: "owner",
  WORKSPACE_PASSWORD_HASH: `scrypt:16384:8:1:${salt.toString("hex")}:${scryptSync(password, salt, 64).toString("hex")}`,
  WORKSPACE_SESSION_SECRET: randomBytes(48).toString("base64url"),
  WORKSPACE_PUBLIC_ORIGIN: "https://workspace.example.com",
};
const config = authConfig(env)!;
const now = Date.UTC(2026, 8, 12, 10);

test("configuration fails closed when required authentication values are absent or malformed", () => {
  for (const key of Object.keys(env).filter((key) => key !== "NODE_ENV")) assert.equal(authConfig({ ...env, [key]: "" }), null, key);
  assert.equal(authConfig({ ...env, WORKSPACE_SESSION_SECRET: "short" }), null);
  assert.equal(authConfig({ ...env, WORKSPACE_PASSWORD_HASH: password }), null);
  assert.equal(authConfig({ ...env, WORKSPACE_PUBLIC_ORIGIN: "http://workspace.example.com" }), null);
  assert.equal(authConfig({ ...env, WORKSPACE_PUBLIC_ORIGIN: "https://workspace.example.com/login" }), null);
  assert.equal(authConfig({ ...env, WORKSPACE_PUBLIC_ORIGIN: "https://user:password@workspace.example.com" }), null);
  assert.ok(config);
});

test("only the configured username and password authenticate", async () => {
  assert.equal(await verifyPassword("owner", password, config), true);
  assert.equal(await verifyPassword("stranger", password, config), false);
  assert.equal(await verifyPassword("owner", `${password}incorrect`, config), false);
  assert.equal(await verifyPassword("owner", { password }, config), false);
  assert.equal(await verifyPassword("owner", "x".repeat(1025), config), false);
});

test("session is random, signed, expires at 8h, and contains no credential", () => {
  const token = createSession(config, now);
  assert.notEqual(token, createSession(config, now));
  assert.ok(verifySession(token, config, now));
  assert.ok(verifySession(token, config, now + SESSION_TTL_SECONDS * 1000 - 1));
  assert.equal(verifySession(token, config, now + SESSION_TTL_SECONDS * 1000), null);
  assert.equal(verifySession(token, config, now - 61000), null);
  const payload = Buffer.from(token.split(".")[0], "base64url").toString();
  for (const secret of [password, config.secret, config.passwordHash, config.username]) assert.equal(payload.includes(secret), false);
});

test("missing, malformed and forged sessions are rejected", () => {
  const token = createSession(config, now);
  for (const invalid of [undefined, "", "x".repeat(2000), "not-a-session", `${token}.extra`, `${token.slice(0, 5)}X${token.slice(6)}`]) assert.equal(verifySession(invalid, config, now), null);
  const [payload, signature] = token.split(".");
  const changed = JSON.parse(Buffer.from(payload, "base64url").toString());
  changed.exp += 86400;
  assert.equal(verifySession(`${Buffer.from(JSON.stringify(changed)).toString("base64url")}.${signature}`, config, now), null);
});

test("changing credentials or the signing secret invalidates existing sessions", () => {
  const token = createSession(config, now);
  assert.equal(verifySession(token, { ...config, username: "new-owner" }, now), null);
  assert.equal(verifySession(token, { ...config, passwordHash: `${config.passwordHash}new` }, now), null);
  assert.equal(verifySession(token, { ...config, secret: randomBytes(48).toString("base64url") }, now), null);
});

test("CSRF proof is bound to the session and cannot be used as a session", () => {
  const first = createSession(config, now), second = createSession(config, now);
  const token = csrfToken(first, config);
  assert.equal(verifyCsrf(first, token, config), true);
  assert.equal(verifyCsrf(second, token, config), false);
  assert.equal(verifyCsrf(first, null, config), false);
  assert.equal(verifyCsrf(first, "attacker", config), false);
  assert.equal(verifySession(token, config, now), null);
});

function request(headers: Record<string, string> = {}) {
  return new Request("https://workspace.example.com/api/leads", { headers: { host: "workspace.example.com", ...headers } });
}

test("writes require exact configured origin including scheme and port", () => {
  assert.equal(allowedOrigin(request({ origin: env.WORKSPACE_PUBLIC_ORIGIN }), config, true), true);
  for (const origin of ["http://workspace.example.com", "https://workspace.example.com:444", "https://workspace.example.com.evil.test", "null", "not-a-url"]) assert.equal(allowedOrigin(request({ origin }), config, true), false, origin);
  assert.equal(allowedOrigin(request(), config, true), false);
});

test("host and Fetch Metadata checks reject cross-site requests", () => {
  assert.equal(allowedOrigin(request(), config), true);
  assert.equal(allowedOrigin(request({ host: "evil.test", origin: env.WORKSPACE_PUBLIC_ORIGIN }), config), false);
  assert.equal(allowedOrigin(request({ "sec-fetch-site": "cross-site" }), config), false);
  assert.equal(allowedOrigin(request({ "sec-fetch-site": "same-site" }), config), false);
  assert.equal(allowedOrigin(request({ "sec-fetch-site": "same-origin" }), config), true);
});

test("development defaults accept only loopback and production uses a host-only secure cookie", () => {
  const local = authConfig({ ...env, NODE_ENV: "development", WORKSPACE_PUBLIC_ORIGIN: "" })!;
  assert.equal(sessionCookieName(config), "__Host-workspace-session");
  assert.equal(sessionCookieName(local), "workspace-session");
  assert.equal(allowedOrigin(new Request("http://localhost:3100/api/leads", { headers: { host: "localhost:3100", origin: "http://localhost:3100" } }), local, true), true);
  assert.equal(allowedOrigin(request(), local), false);
});

test("login attempt limit blocks excess attempts and expires its window", () => {
  const limiter = new LoginLimiter(2, 1000);
  assert.equal(limiter.take("address", now), true);
  assert.equal(limiter.take("address", now + 1), true);
  assert.equal(limiter.take("address", now + 2), false);
  assert.equal(limiter.take("address", now + 1000), true);
  assert.equal(limiter.take("other", now + 1000), true);
});

test("a throttled address cannot consume the remaining shared login budget", () => {
  const perAddress = new LoginLimiter(2, 1000), total = new LoginLimiter(4, 1000);
  assert.equal(allowLoginAttempt("attacker", perAddress, total, now), true);
  assert.equal(allowLoginAttempt("attacker", perAddress, total, now), true);
  for (let index = 0; index < 100; index += 1) assert.equal(allowLoginAttempt("attacker", perAddress, total, now), false);
  assert.equal(allowLoginAttempt("owner", perAddress, total, now), true);
  assert.equal(allowLoginAttempt("owner", perAddress, total, now), true);
  assert.equal(allowLoginAttempt("another-source", perAddress, total, now), false);
});
