import { getAuthConfig } from "@/lib/auth";
import { backendOrigin } from "@/lib/proxy-policy";
import { backendReady } from "@/lib/readiness";
import { workspaceBackend } from "@/lib/workspace-mode";
import { workspaceReady } from "@/lib/workspace-db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const config = getAuthConfig();
  const mode = workspaceBackend(process.env);
  let ready = false;
  if (config && mode) {
    try {
      if (mode === "postgres") ready = await workspaceReady();
      else {
        const base = backendOrigin(process.env.BACKEND_URL, config.secure, process.env.BACKEND_INTERNAL === "true");
        ready = Boolean(process.env.WORKSPACE_API_KEY && await backendReady(base, config.origin));
      }
    } catch { ready = false; }
  }
  return Response.json({ status: ready ? "ready" : "unavailable" }, { status: ready ? 200 : 503, headers: { "Cache-Control": "no-store" } });
}
