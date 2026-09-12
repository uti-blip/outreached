import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { test } from "node:test";
import { LOGIN_CLIENT_LIMIT, LOGIN_GLOBAL_LIMIT, LOGIN_MAX_CLIENTS, LOGIN_WINDOW_MS, loginClientKey, nextLoginRateLimit } from "../src/lib/login-rate-limit-core.ts";
import { workspaceBackend } from "../src/lib/workspace-mode.ts";

const now = Date.UTC(2026, 8, 12, 10);
const key = (value: string) => createHash("sha256").update(value).digest("hex");
const secret = randomBytes(48).toString("base64url");
const request = (headers: Record<string, string>) => new Request("https://workspace.example/api/auth/login", { headers });

test("only a valid Netlify connection address contributes an opaque source key", () => {
  const headers = { "x-nf-client-connection-ip": "203.0.113.4", "x-forwarded-for": "198.51.100.9" };
  const actual = loginClientKey(request(headers), secret, { NETLIFY: "true" });
  assert.match(actual!, /^[a-f0-9]{64}$/);
  assert.equal(actual, loginClientKey(request({ ...headers, "x-forwarded-for": "attacker" }), secret, { NETLIFY: "true" }));
  assert.notEqual(actual, loginClientKey(request(headers), `${secret}rotated`, { NETLIFY: "true" }));
  for (const env of [{}, { NETLIFY: "false" }, { NETLIFY: "1" }]) assert.equal(loginClientKey(request(headers), secret, env), null);
  for (const address of ["", "unknown", "203.0.113.4,198.51.100.9", "203.0.113.4:1234", "fe80::1%eth0", "x".repeat(100)]) {
    assert.equal(loginClientKey(request({ "x-nf-client-connection-ip": address }), secret, { NETLIFY: "true" }), null, address);
  }
  assert.equal(loginClientKey(request({ "x-forwarded-for": "203.0.113.4" }), secret, { NETLIFY: "true" }), null);
  assert.equal(loginClientKey(request({ "x-nf-client-connection-ip": "2001:db8::1" }), secret, { NETLIFY: "true" }), loginClientKey(request({ "x-nf-client-connection-ip": "2001:0db8:0:0:0:0:0:1" }), secret, { NETLIFY: "true" }));
});

test("source throttling persists in serialized shared state and does not spend other users' budget", () => {
  let state: unknown = {};
  for (let index = 0; index < LOGIN_CLIENT_LIMIT; index += 1) {
    const result = nextLoginRateLimit(state, key("attacker"), now + index);
    assert.equal(result.allowed, true);
    state = JSON.parse(JSON.stringify(result.state));
  }
  for (let index = 0; index < 100; index += 1) {
    const result = nextLoginRateLimit(state, key("attacker"), now + 100);
    assert.equal(result.allowed, false);
    assert.equal(result.state.global.count, LOGIN_CLIENT_LIMIT);
    state = JSON.parse(JSON.stringify(result.state));
  }
  const owner = nextLoginRateLimit(state, key("owner"), now + 200);
  assert.equal(owner.allowed, true);
  assert.equal(owner.state.global.count, LOGIN_CLIENT_LIMIT + 1);
  assert.equal(nextLoginRateLimit(owner.state, key("attacker"), now + LOGIN_WINDOW_MS - 1).allowed, false);
  const expired = nextLoginRateLimit(owner.state, key("attacker"), now + LOGIN_WINDOW_MS);
  assert.equal(expired.allowed, true);
  assert.equal(expired.state.global.count, 1);
});

test("missing trusted source still enforces a shared global limit with bounded storage", () => {
  let state: unknown = {};
  for (let index = 0; index < LOGIN_GLOBAL_LIMIT; index += 1) {
    const result = nextLoginRateLimit(state, null, now);
    assert.equal(result.allowed, true);
    state = JSON.parse(JSON.stringify(result.state));
  }
  const blocked = nextLoginRateLimit(state, null, now + LOGIN_WINDOW_MS - 1);
  assert.equal(blocked.allowed, false);
  assert.deepEqual(blocked.state.clients, {});
  assert.equal(nextLoginRateLimit(blocked.state, key("new-address"), now + 100).allowed, false);
  assert.equal(nextLoginRateLimit(blocked.state, null, now + LOGIN_WINDOW_MS).allowed, true);
});

test("rotating addresses cannot evade the global budget or create unbounded stored keys", () => {
  let state: unknown = {};
  for (let index = 0; index < 2000; index += 1) {
    const result = nextLoginRateLimit(state, key(`source-${index}`), now);
    assert.equal(result.allowed, index < LOGIN_GLOBAL_LIMIT);
    state = result.state;
  }
  assert.equal(Object.keys((state as { clients: object }).clients).length, LOGIN_GLOBAL_LIMIT);
  const full = { v: 1, global: { count: 0, reset: now + 100 }, clients: Object.fromEntries(Array.from({ length: LOGIN_MAX_CLIENTS }, (_, index) => [key(String(index)), { count: 1, reset: now + 10 }])) };
  assert.equal(nextLoginRateLimit(full, key("another"), now).allowed, false);
  const expired = nextLoginRateLimit(full, key("another"), now + 10);
  assert.equal(expired.allowed, true);
  assert.equal(Object.keys(expired.state.clients).length, 1);
});

test("malformed shared state and raw source addresses fail closed", () => {
  const valid = nextLoginRateLimit({}, null, now).state;
  for (const state of [null, [], "{}", { v: 2 }, { ...valid, global: { count: -1, reset: now } }, { ...valid, global: { count: 1.5, reset: now } }, { ...valid, clients: { "203.0.113.4": { count: 1, reset: now } } }, { ...valid, extra: true }, { ...valid, clients: Object.fromEntries(Array.from({ length: LOGIN_MAX_CLIENTS + 1 }, (_, index) => [key(String(index)), { count: 1, reset: now + 10 }])) }]) assert.throws(() => nextLoginRateLimit(state, null, now));
  assert.throws(() => nextLoginRateLimit({}, "203.0.113.4", now));
  assert.throws(() => nextLoginRateLimit({}, null, NaN));
  assert.throws(() => nextLoginRateLimit({}, null, -1));
});

test("an invalid backend selection never silently changes storage mode", () => {
  assert.equal(workspaceBackend({}), "proxy");
  assert.equal(workspaceBackend({ WORKSPACE_BACKEND: "proxy" }), "proxy");
  assert.equal(workspaceBackend({ WORKSPACE_BACKEND: "postgres" }), "postgres");
  for (const mode of ["POSTGRES", "postgres ", "sqlite"]) assert.equal(workspaceBackend({ WORKSPACE_BACKEND: mode }), null);
});
