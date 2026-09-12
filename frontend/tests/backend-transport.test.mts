import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { loopbackRequest } from "../src/lib/backend-transport.ts";

test("the actual internal HTTP request preserves the validated Host and request body", async () => {
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => { response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify({ host: request.headers.host, method: request.method, body })); });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const response = await loopbackRequest(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/profile`, { method: "PUT", headers: { Host: "workspace.example.test", "Content-Type": "application/json" }, body: new Uint8Array(Buffer.from('{"company":"Example"}')), signal: AbortSignal.timeout(2000) });
    assert.deepEqual(await response.json(), { host: "workspace.example.test", method: "PUT", body: '{"company":"Example"}' });
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); }
});

test("internal transport rejects redirects and non-loopback targets", async () => {
  await assert.rejects(loopbackRequest("http://api.example.com", {}));
  const server = createServer((_request, response) => { response.writeHead(302, { Location: "https://api.example.com" }); response.end(); });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await assert.rejects(loopbackRequest(`http://127.0.0.1:${(server.address() as AddressInfo).port}`, { signal: AbortSignal.timeout(2000) }), /redirects are forbidden/);
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); }
});
