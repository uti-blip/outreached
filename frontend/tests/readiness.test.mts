import assert from "node:assert/strict";
import { test } from "node:test";
import { backendReady } from "../src/lib/readiness.ts";
import { INTERNAL_BACKEND_ORIGIN } from "../src/lib/proxy-policy.ts";

test("readiness makes a bounded, read-only internal database probe with the configured host", async () => {
  const request: typeof fetch = async (url, options) => {
    assert.equal(url, `${INTERNAL_BACKEND_ORIGIN}/health/ready`);
    assert.equal(options?.method, "GET");
    assert.deepEqual(options?.headers, { Host: "workspace.example.com" });
    assert.equal(options?.redirect, "error");
    assert.equal(options?.cache, "no-store");
    assert.ok(options?.signal);
    return Response.json({ status: "ready" });
  };
  assert.equal(await backendReady(INTERNAL_BACKEND_ORIGIN, "https://workspace.example.com", request), true);
});

test("readiness fails closed for unavailable, malformed and unreachable backend", async () => {
  for (const response of [Response.json({ status: "unavailable" }, { status: 503 }), Response.json({ status: "ok" }), Response.json(null), new Response("not-json")]) {
    assert.equal(await backendReady(INTERNAL_BACKEND_ORIGIN, "https://workspace.example.com", async () => response), false);
  }
  assert.equal(await backendReady(INTERNAL_BACKEND_ORIGIN, "https://workspace.example.com", async () => { throw new Error("offline"); }), false);
  assert.equal(await backendReady(null, "https://workspace.example.com", async () => { throw new Error("must not be called"); }), false);
});
