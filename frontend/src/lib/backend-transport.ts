import { request as httpRequest } from "node:http";
import { Readable } from "node:stream";
import { INTERNAL_BACKEND_ORIGIN } from "./proxy-policy.ts";

/** Native fetch strips Host. The isolated API needs the validated public Host. */
export function loopbackRequest(url: string, options: RequestInit): Promise<Response> {
  const target = new URL(url);
  if (target.protocol !== "http:" || target.hostname !== "127.0.0.1" || target.username || target.password) return Promise.reject(new Error("Invalid internal transport target"));
  const body = options.body;
  if (body !== undefined && body !== null && typeof body !== "string" && !(body instanceof Uint8Array)) return Promise.reject(new Error("Unsupported internal request body"));
  return new Promise((resolve, reject) => {
    const request = httpRequest(target, {
      method: options.method,
      headers: Object.fromEntries(new Headers(options.headers)),
      signal: options.signal ?? undefined,
    }, (upstream) => {
      const status = upstream.statusCode ?? 502;
      if (status >= 300 && status < 400) { upstream.destroy(); reject(new Error("Internal redirects are forbidden")); return; }
      const headers = new Headers();
      for (const [key, value] of Object.entries(upstream.headers)) {
        if (Array.isArray(value)) for (const item of value) headers.append(key, item);
        else if (value !== undefined) headers.set(key, value);
      }
      const stream = [204, 205, 304].includes(status) ? null : Readable.toWeb(upstream) as ReadableStream<Uint8Array>;
      if (!stream) upstream.resume();
      resolve(new Response(stream, { status, headers }));
    });
    request.once("error", reject);
    request.end(body ?? undefined);
  });
}

export function backendFetch(url: string, options: RequestInit): Promise<Response> {
  return new URL(url).origin === INTERNAL_BACKEND_ORIGIN ? loopbackRequest(url, options) : fetch(url, options);
}
