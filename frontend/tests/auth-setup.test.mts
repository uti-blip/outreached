import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { authConfig, verifyPassword } from "../src/lib/auth-core.ts";

const script = fileURLToPath(new URL("../scripts/setup-auth.mjs", import.meta.url));

test("combined deployment setup emits private files and explicit internal mode without printing secrets", () => {
  const directory = mkdtempSync(join(tmpdir(), "outreached-auth-test-"));
  try {
    const result = spawnSync(process.execPath, [script, "--output", directory, "--origin", "https://workspace.example.test", "--internal"], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    const env = readFileSync(join(directory, "frontend.env"), "utf8");
    const credentials = readFileSync(join(directory, "workspace-login.txt"), "utf8");
    assert.match(env, /BACKEND_INTERNAL='true'/);
    assert.match(env, /BACKEND_URL='http:\/\/127\.0\.0\.1:8001'/);
    assert.equal(statSync(join(directory, "frontend.env")).mode & 0o777, 0o600);
    assert.equal(statSync(join(directory, "workspace-login.txt")).mode & 0o777, 0o600);
    const password = credentials.match(/Password: (.+)/)![1];
    assert.equal(result.stdout.includes(password), false);
    const repeated = spawnSync(process.execPath, [script, "--output", directory], { encoding: "utf8" });
    assert.notEqual(repeated.status, 0);
    assert.equal(readFileSync(join(directory, "frontend.env"), "utf8"), env);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("setup never silently enables an HTTP remote backend in production", () => {
  const directory = mkdtempSync(join(tmpdir(), "outreached-auth-test-"));
  try {
    const insecure = spawnSync(process.execPath, [script, "--output", directory, "--origin", "https://workspace.example.test", "--backend-url", "http://remote.example.test"], { encoding: "utf8" });
    assert.notEqual(insecure.status, 0);
    const override = spawnSync(process.execPath, [script, "--output", directory, "--origin", "https://workspace.example.test", "--backend-url", "http://remote.example.test", "--internal"], { encoding: "utf8" });
    assert.notEqual(override.status, 0);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("PostgreSQL setup generates usable private authentication without Python proxy secrets", async () => {
  const directory = mkdtempSync(join(tmpdir(), "outreached-auth-test-"));
  try {
    const result = spawnSync(process.execPath, [script, "--output", directory, "--origin", "https://workspace.example.test", "--user", "owner", "--postgres"], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    const envText = readFileSync(join(directory, "frontend.env"), "utf8");
    const env = Object.fromEntries(envText.trim().split("\n").map((line) => { const match = /^([A-Z_]+)='(.*)'$/.exec(line)!; return [match[1], match[2]]; }));
    assert.equal(env.WORKSPACE_BACKEND, "postgres");
    for (const name of ["WORKSPACE_API_KEY", "SECRET_KEY", "BACKEND_URL", "BACKEND_INTERNAL", "WORKSPACE_DATABASE_URL"]) assert.equal(name in env, false, name);
    const config = authConfig({ ...env, NODE_ENV: "production" });
    assert.ok(config);
    const credentials = readFileSync(join(directory, "workspace-login.txt"), "utf8");
    const password = credentials.match(/Password: (.+)/)![1];
    assert.equal(await verifyPassword("owner", password, config), true);
    assert.equal(statSync(join(directory, "frontend.env")).mode & 0o777, 0o600);
    assert.equal(statSync(join(directory, "workspace-login.txt")).mode & 0o777, 0o600);
    for (const value of [password, config.passwordHash, config.secret]) assert.equal(`${result.stdout}${result.stderr}`.includes(value), false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("PostgreSQL setup refuses contradictory proxy flags", () => {
  const directory = mkdtempSync(join(tmpdir(), "outreached-auth-test-"));
  try {
    for (const flags of [["--internal"], ["--backend-url", "https://backend.example.test"]]) {
      const result = spawnSync(process.execPath, [script, "--output", directory, "--postgres", ...flags], { encoding: "utf8" });
      assert.notEqual(result.status, 0);
      assert.throws(() => readFileSync(join(directory, "frontend.env")));
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
