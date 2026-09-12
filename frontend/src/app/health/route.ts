import { getAuthConfig } from "@/lib/auth";
import { backendOrigin } from "@/lib/proxy-policy";
import { backendReady } from "@/lib/readiness";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const config = getAuthConfig();
  const base = config && backendOrigin(process.env.BACKEND_URL, config.secure, process.env.BACKEND_INTERNAL === "true");
  const ready = Boolean(config && process.env.WORKSPACE_API_KEY && await backendReady(base, config.origin));
  return Response.json({ status: ready ? "ready" : "unavailable" }, { status: ready ? 200 : 503, headers: { "Cache-Control": "no-store" } });
}
