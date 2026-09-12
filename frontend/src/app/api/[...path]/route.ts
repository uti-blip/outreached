import { type NextRequest, NextResponse } from "next/server";
import { authorize, authError, readLimitedBody } from "@/lib/auth";
import { allowedProxyRoute, backendHostHeader, backendOrigin } from "@/lib/proxy-policy";
import { backendFetch } from "@/lib/backend-transport";
import { workspaceBackend } from "@/lib/workspace-mode";
import { handleWorkspaceRequest } from "@/lib/workspace-service";

export const runtime = "nodejs";

/**
 * Authenticated workspace API: direct PostgreSQL or the original Python proxy.
 *
 * The workspace secret is NEVER sent to the browser: it is read from the
 * server-only environment here and injected as the Bearer token. Any
 * Authorization header coming from the browser is discarded. Every call
 * requires a valid workspace session; writes additionally require CSRF proof.
 */
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const auth = authorize(request);
  if (auth instanceof NextResponse) return auth;
  const mode = workspaceBackend(process.env);
  if (!mode) return authError("Cet espace est temporairement indisponible. Contactez son administrateur.", 503);
  const { path } = await context.params;
  if (path.some((segment) => !/^[a-zA-Z0-9_.-]+$/.test(segment) || segment === "." || segment === "..")) {
    return authError("Chemin invalide", 400);
  }
  if (!allowedProxyRoute(request.method, path)) return authError("Ressource introuvable.", 404);
  try {
    const body = await readLimitedBody(request, 1500000);
    if (!body) return authError("Fichier trop volumineux (1 Mo maximum)", 413);
    let response: Response;
    if (mode === "postgres") {
      response = await handleWorkspaceRequest(request.method, path, request.nextUrl.searchParams, body);
    } else {
      const workspaceKey = process.env.WORKSPACE_API_KEY;
      const base = backendOrigin(process.env.BACKEND_URL, auth.config.secure, process.env.BACKEND_INTERNAL === "true");
      if (!workspaceKey || !base) return authError("Cet espace est temporairement indisponible. Contactez son administrateur.", 503);
      const url = `${base.replace(/\/$/, "")}/api/${path.join("/")}${request.nextUrl.search}`;
      response = await backendFetch(url, {
        method: request.method,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${workspaceKey}`, ...backendHostHeader(base, auth.config.origin) },
        body: body.length ? new Uint8Array(body) : undefined,
        signal: AbortSignal.timeout(20000), cache: "no-store", redirect: "error",
      });
    }
    if (response.status >= 500 || response.status === 401 || response.status === 403) {
      await response.body?.cancel();
      return authError("Cet espace est temporairement indisponible. Réessayez plus tard.", mode === "postgres" ? 503 : 502);
    }
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff",
      ...(response.headers.get("content-disposition") ? { "Content-Disposition": response.headers.get("content-disposition")! } : {}),
    } });
  } catch {
    return authError("Le serveur est temporairement indisponible. Réessayez plus tard.", mode === "postgres" ? 503 : 502);
  }
}

export { proxy as GET, proxy as POST, proxy as PUT };
