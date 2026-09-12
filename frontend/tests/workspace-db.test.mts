import assert from "node:assert/strict";
import { inspect } from "node:util";
import type { ConnectionOptions, PeerCertificate } from "node:tls";
import { after, test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { Pool } from "pg";
import { closeWorkspacePool, withWorkspace, workspacePoolOptions, workspaceReady, WorkspaceStoreError } from "../src/lib/workspace-db.ts";

test("PostgreSQL options verify TLS, pin the URL host and keep credentials out of inspection", () => {
  const password = "synthetic%40secret%3Avalue";
  const options = workspacePoolOptions({ WORKSPACE_DATABASE_URL: `postgresql://outreached_app:${password}@db.example.test/workspace?sslmode=verify-full` });
  assert.equal(options.password, "synthetic@secret:value");
  assert.equal(options.host, "db.example.test");
  assert.equal(options.max, 1);
  assert.equal(options.connectionTimeoutMillis, 5000);
  assert.equal(options.query_timeout, 18000);
  assert.equal(options.connectionString, undefined);
  assert.ok(!inspect(options).includes("synthetic"));
  assert.ok(!JSON.stringify(options).includes("synthetic"));
  const tls = options.ssl as ConnectionOptions;
  assert.equal(tls.rejectUnauthorized, true);
  const matching = { subjectaltname: "DNS:db.example.test" } as PeerCertificate;
  const other = { subjectaltname: "DNS:attacker.example.test" } as PeerCertificate;
  assert.equal(tls.checkServerIdentity!("untrusted-caller-name", matching), undefined);
  assert.ok(tls.checkServerIdentity!("attacker.example.test", other) instanceof Error);
});

test("URL overrides, incomplete credentials, downgrade and unsupported binding requirements fail closed", () => {
  const valid = "postgresql://outreached_app:synthetic-secret@db.example.test/workspace";
  for (const suffix of ["?sslmode=disable", "?sslmode=require", "?sslmode=verify-ca", "?sslmode=prefer", "?sslmode=no-verify", "?sslmode=verify-full&sslmode=disable", "?host=attacker.test", "?options=-c%20search_path=public", "?ssl=false", "?application_name=other", "?channel_binding=require", "?channel_binding=other", "#fragment"]) {
    assert.throws(() => workspacePoolOptions({ WORKSPACE_DATABASE_URL: valid + suffix }), WorkspaceStoreError);
  }
  for (const value of ["", "postgresql://host/db", "postgresql://user:secret@host/", "postgresql://user:@host/db", "postgresql://user:secret@host/db/other", "https://user:secret@host/db", "postgresql://user:secret@host:65536/db", valid + " "]) {
    assert.throws(() => workspacePoolOptions({ WORKSPACE_DATABASE_URL: value }), (error: unknown) => error instanceof WorkspaceStoreError && !String(error).includes("secret"));
  }
  assert.throws(() => workspacePoolOptions({ WORKSPACE_DATABASE_URL: valid + "?sslrootcert=/missing-root-ca" }), WorkspaceStoreError);
});

test("only an explicit nonproduction loopback connection can disable TLS", () => {
  for (const host of ["127.0.0.1", "127.0.0.2", "localhost", "[::1]"]) {
    const WORKSPACE_DATABASE_URL = `postgres://user:password@${host}:5432/outreached_test?sslmode=disable`;
    assert.equal(workspacePoolOptions({ WORKSPACE_DATABASE_URL, NODE_ENV: "test" }).ssl, false);
    assert.throws(() => workspacePoolOptions({ WORKSPACE_DATABASE_URL, NODE_ENV: "production" }), WorkspaceStoreError);
    assert.throws(() => workspacePoolOptions({ WORKSPACE_DATABASE_URL, APP_ENV: "production" }), WorkspaceStoreError);
  }
  const options = workspacePoolOptions({ WORKSPACE_DATABASE_URL: "postgres://user:password@127.0.0.1/outreached_test" });
  assert.equal((options.ssl as ConnectionOptions).rejectUnauthorized, true);
});

const testUrl = process.env.WORKSPACE_TEST_POSTGRES_URL;
const testOptions = { skip: !testUrl, concurrency: false };

function ownerPool(): Pool {
  const url = new URL(testUrl!);
  assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
  assert.equal(url.pathname, "/outreached_test", "Integration checks only accept the disposable test database");
  return new Pool({ ...workspacePoolOptions({ WORKSPACE_DATABASE_URL: testUrl, NODE_ENV: "test" }), password: decodeURIComponent(url.password), max: 2 });
}

after(closeWorkspacePool);

test("real PostgreSQL writes serialize before reads and read-only snapshots cannot mutate", testOptions, async () => {
  const owner = ownerPool();
  try {
    assert.equal(await workspaceReady(), true);
    await owner.query("TRUNCATE outreached_workspace.profile");
    const returned = await Promise.all(Array.from({ length: 12 }, () => withWorkspace(true, async (db) => {
      const before = await db.query("SELECT data FROM profile WHERE id=1");
      const current = before.rows.length ? Number(JSON.parse(before.rows[0].data).count) : 0;
      await db.query("INSERT INTO profile(id,data) VALUES(1,$1) ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data", [JSON.stringify({ count: current + 1 })]);
      return current + 1;
    })));
    assert.deepEqual(returned.toSorted((a, b) => a - b), Array.from({ length: 12 }, (_, index) => index + 1));
    await withWorkspace(false, async (db) => {
      const first = await db.query("SELECT data FROM profile WHERE id=1");
      await owner.query("UPDATE outreached_workspace.profile SET data=$1 WHERE id=1", [JSON.stringify({ count: 99 })]);
      const second = await db.query("SELECT data FROM profile WHERE id=1");
      assert.equal(first.rows[0].data, second.rows[0].data);
    });
    await assert.rejects(withWorkspace(false, (db) => db.query("UPDATE profile SET data='{}' WHERE id=1")), WorkspaceStoreError);
    await assert.rejects(withWorkspace(true, (db) => db.query("DELETE FROM profile")), WorkspaceStoreError);
  } finally { await owner.end(); }
});

test("real PostgreSQL rolls back on business errors and never exposes driver detail", testOptions, async () => {
  const owner = ownerPool();
  const sentinel = new Error("business validation");
  try {
    await owner.query("TRUNCATE outreached_workspace.profile");
    await assert.rejects(withWorkspace(true, async (db) => {
      await db.query("INSERT INTO profile(id,data) VALUES(1,'{}')");
      throw sentinel;
    }), (error) => error === sentinel);
    assert.equal((await owner.query("SELECT COUNT(*) FROM outreached_workspace.profile")).rows[0].count, "0");
    await assert.rejects(withWorkspace(true, (db) => db.query("SELECT $1::integer", ["secret-row-value"])), (error: unknown) => {
      assert.ok(error instanceof WorkspaceStoreError);
      assert.ok(!inspect(error).includes("secret-row-value"));
      assert.equal(error.cause, undefined);
      return true;
    });
    assert.equal(await workspaceReady(), true);
  } finally { await owner.end(); }
});

test("real PostgreSQL writer waits on another connection and then sees its committed state", testOptions, async () => {
  const owner = ownerPool();
  const blocker = await owner.connect();
  let pending: Promise<number> | undefined;
  try {
    await owner.query("INSERT INTO outreached_workspace.profile(id,data) VALUES(1,'1') ON CONFLICT(id) DO UPDATE SET data='1'");
    await blocker.query("BEGIN");
    await blocker.query("SELECT pg_advisory_xact_lock(1869968498,1)");
    await blocker.query("UPDATE outreached_workspace.profile SET data='2' WHERE id=1");
    pending = withWorkspace(true, async (db) => Number((await db.query("SELECT data FROM profile WHERE id=1")).rows[0].data));
    let waiting = false;
    for (let attempt = 0; attempt < 50 && !waiting; attempt++) {
      waiting = (await owner.query("SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND NOT l.granted AND a.usename='outreached_app') AS waiting")).rows[0].waiting;
      if (!waiting) await delay(10);
    }
    assert.equal(waiting, true, "The application must wait on the cross-instance advisory lock");
    await blocker.query("COMMIT");
    assert.equal(await pending, 2, "The post-lock read must include the previous writer's commit");
  } finally {
    await blocker.query("ROLLBACK");
    await pending?.catch(() => {});
    blocker.release();
    await owner.end();
  }
});

test("real PostgreSQL disconnect during local work is sanitized and the next request recovers", testOptions, async () => {
  const owner = ownerPool();
  try {
    await assert.rejects(withWorkspace(true, async (db) => {
      const pid = (await db.query("SELECT pg_backend_pid() AS pid")).rows[0].pid;
      await owner.query("SELECT pg_terminate_backend($1)", [pid]);
      await delay(20);
    }), WorkspaceStoreError);
    assert.equal(await workspaceReady(), true);
  } finally { await owner.end(); }
});

test("real PostgreSQL readiness refuses owner credentials, ACL drift and missing limiter table", testOptions, async () => {
  const owner = ownerPool();
  const runtimeUrl = process.env.WORKSPACE_DATABASE_URL;
  try {
    process.env.WORKSPACE_DATABASE_URL = testUrl;
    assert.equal(await workspaceReady(), false);
    process.env.WORKSPACE_DATABASE_URL = runtimeUrl;
    await owner.query("GRANT DELETE ON outreached_workspace.leads TO outreached_app");
    assert.equal(await workspaceReady(), false);
    await owner.query("REVOKE DELETE ON outreached_workspace.leads FROM outreached_app");
    assert.equal(await workspaceReady(), true);
    await owner.query("ALTER TABLE outreached_workspace.login_rate_limit RENAME TO hidden_login_rate_limit");
    assert.equal(await workspaceReady(), false);
    await owner.query("ALTER TABLE outreached_workspace.hidden_login_rate_limit RENAME TO login_rate_limit");
    assert.equal(await workspaceReady(), true);
    await owner.query("DELETE FROM outreached_workspace.login_rate_limit WHERE id=1");
    assert.equal(await workspaceReady(), false);
    await owner.query("INSERT INTO outreached_workspace.login_rate_limit(id,state) VALUES(1,'{}')");
    await owner.query("UPDATE outreached_workspace.login_rate_limit SET state=$1::jsonb WHERE id=1", [JSON.stringify({ bad: true })]);
    assert.equal(await workspaceReady(), false);
    await owner.query("UPDATE outreached_workspace.login_rate_limit SET state='{}' WHERE id=1");
    assert.equal(await workspaceReady(), true);
  } finally {
    process.env.WORKSPACE_DATABASE_URL = runtimeUrl;
    await owner.query("REVOKE DELETE ON outreached_workspace.leads FROM outreached_app");
    await owner.query("INSERT INTO outreached_workspace.login_rate_limit(id,state) VALUES(1,'{}') ON CONFLICT(id) DO UPDATE SET state='{}'");
    await owner.end();
  }
});
