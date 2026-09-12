import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import { workspacePoolOptions } from "../src/lib/workspace-db.ts";

const ownerUrl = process.env.WORKSPACE_TEST_POSTGRES_URL;
const runtimeUrl = process.env.WORKSPACE_DATABASE_URL;
const secret = randomBytes(48).toString("base64url");
const limiterModule = new URL("../src/lib/login-rate-limit.ts", import.meta.url).href;
const storeModule = new URL("../src/lib/workspace-db.ts", import.meta.url).href;

function attempts(source: string, count: number, databaseUrl = runtimeUrl): Promise<{ allowed: number; unavailable: number }> {
  const program = `
    import { takeDistributedLoginAttempt } from ${JSON.stringify(limiterModule)};
    import { closeWorkspacePool } from ${JSON.stringify(storeModule)};
    const request = new Request('https://workspace.example/api/auth/login', { headers: { 'x-nf-client-connection-ip': process.env.TEST_SOURCE } });
    let allowed = 0, unavailable = 0;
    try {
      for (let index = 0; index < Number(process.env.TEST_ATTEMPTS); index++) {
        try { if (await takeDistributedLoginAttempt(request, process.env.TEST_SECRET)) allowed++; }
        catch { unavailable++; }
      }
      process.stdout.write(JSON.stringify({allowed, unavailable}));
    } finally { await closeWorkspacePool(); }
  `;
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--conditions=react-server", "--experimental-strip-types", "--input-type=module", "-e", program], {
      env: { ...process.env, WORKSPACE_DATABASE_URL: databaseUrl, NETLIFY: "true", TEST_SOURCE: source, TEST_ATTEMPTS: String(count), TEST_SECRET: secret },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "", error = "";
    child.stdout.on("data", (value) => { output += value; });
    child.stderr.on("data", (value) => { error += value; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) { reject(new Error(`Limiter subprocess failed (${code}): ${error}`)); return; }
      try { resolve(JSON.parse(output)); } catch { reject(new Error("Limiter subprocess returned invalid output.")); }
    });
  });
}

test("PostgreSQL login budgets are atomic across independent processes and survive fresh instances", { skip: !ownerUrl || !runtimeUrl }, async () => {
  for (const source of [ownerUrl!, runtimeUrl!]) {
    const parsed = new URL(source);
    assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname));
    assert.equal(parsed.pathname, "/outreached_test", "Only the disposable database may be used");
  }
  const parsed = new URL(ownerUrl!);
  const owner = new Pool({ ...workspacePoolOptions({ WORKSPACE_DATABASE_URL: ownerUrl, NODE_ENV: "test" }), password: decodeURIComponent(parsed.password) });
  let original: unknown;
  try {
    original = (await owner.query("SELECT state FROM outreached_workspace.login_rate_limit WHERE id=1")).rows[0].state;
    await owner.query("UPDATE outreached_workspace.login_rate_limit SET state='{}'::jsonb WHERE id=1");
    const concurrent = await Promise.all(Array.from({ length: 3 }, () => attempts("203.0.113.4", 4)));
    assert.equal(concurrent.reduce((sum, result) => sum + result.allowed, 0), 8);
    assert.equal(concurrent.reduce((sum, result) => sum + result.unavailable, 0), 0);
    assert.deepEqual(await attempts("203.0.113.4", 3), { allowed: 0, unavailable: 0 });
    assert.deepEqual(await attempts("198.51.100.9", 3), { allowed: 3, unavailable: 0 });
    const state = (await owner.query("SELECT state FROM outreached_workspace.login_rate_limit WHERE id=1")).rows[0].state;
    assert.equal(state.global.count, 11);
    assert.equal(Object.keys(state.clients).length, 2);
    assert.ok(Object.keys(state.clients).every((key) => /^[a-f0-9]{64}$/.test(key)));
    for (const value of ["203.0.113.4", "198.51.100.9", secret]) assert.equal(JSON.stringify(state).includes(value), false);

    // A removed limiter row or unreachable DB cannot become an authentication bypass.
    await owner.query("DELETE FROM outreached_workspace.login_rate_limit WHERE id=1");
    assert.deepEqual(await attempts("192.0.2.5", 1), { allowed: 0, unavailable: 1 });
    const unavailableUrl = new URL(runtimeUrl!);
    unavailableUrl.port = "1";
    assert.deepEqual(await attempts("192.0.2.5", 1, unavailableUrl.href), { allowed: 0, unavailable: 1 });
  } finally {
    if (original !== undefined) await owner.query("INSERT INTO outreached_workspace.login_rate_limit(id,state) VALUES(1,$1::jsonb) ON CONFLICT(id) DO UPDATE SET state=EXCLUDED.state", [JSON.stringify(original)]);
    await owner.end();
  }
});
