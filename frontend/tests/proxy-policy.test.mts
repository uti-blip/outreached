import assert from "node:assert/strict";
import { test } from "node:test";
import { allowedProxyRoute, backendOrigin } from "../src/lib/proxy-policy.ts";

test("only manual-workspace API methods and routes are exposed", () => {
  for (const [method, path] of [["GET", "workspace"], ["GET", "export/backup.json"], ["POST", "leads/import"], ["POST", "drafts/abc-123/prepare"], ["PUT", "profile"]]) assert.equal(allowedProxyRoute(method, path.split("/")), true);
  for (const [method, path] of [["POST", "campaign/run"], ["GET", "profile"], ["DELETE", "leads/123"], ["GET", "../health"], ["GET", "leads/./detail"], ["GET", "auth/session"], ["POST", "campaigns/anything"]]) assert.equal(allowedProxyRoute(method, path.split("/")), false);
});

test("production never sends the workspace credential to an unconfigured or HTTP backend", () => {
  assert.equal(backendOrigin(undefined, true), null);
  assert.equal(backendOrigin("http://api.example.com", true), null);
  assert.equal(backendOrigin("https://api.example.com", true), "https://api.example.com");
  assert.equal(backendOrigin("https://username:secret@api.example.com", true), null);
  assert.equal(backendOrigin("https://api.example.com/unexpected", true), null);
  assert.equal(backendOrigin(undefined, false), "http://127.0.0.1:8001");
});
