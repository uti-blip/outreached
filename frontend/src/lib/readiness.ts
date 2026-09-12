import { backendHostHeader } from "./proxy-policy.ts";
import { backendFetch } from "./backend-transport.ts";

export async function backendReady(base: string | null, publicOrigin: string | null, request: (url: string, options: RequestInit) => Promise<Response> = backendFetch): Promise<boolean> {
  if (!base || !publicOrigin) return false;
  try {
    const response = await request(`${base}/health/ready`, {
      method: "GET", headers: backendHostHeader(base, publicOrigin),
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) { await response.body?.cancel(); return false; }
    const body: unknown = await response.json();
    return Boolean(body && typeof body === "object" && "status" in body && body.status === "ready");
  } catch { return false; }
}
