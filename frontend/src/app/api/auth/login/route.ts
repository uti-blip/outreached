import { type NextRequest, NextResponse } from "next/server";
import { authError, getAuthConfig, readLimitedBody } from "@/lib/auth";
import { allowedOrigin, allowLoginAttempt, createSession, LoginLimiter, sessionCookieName, SESSION_TTL_SECONDS, verifyPassword } from "@/lib/auth-core";

export const runtime = "nodejs";
const perAddress = new LoginLimiter();
const total = new LoginLimiter(60);

export async function POST(request: NextRequest) {
  const config = getAuthConfig();
  if (!config) return authError("La connexion est temporairement indisponible. Contactez votre administrateur.", 503);
  if (!allowedOrigin(request, config, true) || request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") return authError("Requête non autorisée.", 403);
  // Best-effort, per-instance abuse protection. Deployment ingress must also rate-limit /api/auth/login.
  const address = (request.headers.get("x-forwarded-for") || "unknown").split(",")[0].trim().slice(0, 100);
  if (!allowLoginAttempt(address, perAddress, total)) {
    const response = authError("Trop de tentatives. Réessayez dans 15 minutes.", 429);
    response.headers.set("Retry-After", "900");
    return response;
  }
  try {
    const body = await readLimitedBody(request, 4096);
    if (!body) return authError("Requête trop volumineuse.", 413);
    let value: unknown;
    try { value = JSON.parse(body.toString()); } catch { return authError("Requête invalide.", 400); }
    if (!value || typeof value !== "object") return authError("Identifiant ou mot de passe incorrect.", 401);
    const { username, password } = value as Record<string, unknown>;
    if (!await verifyPassword(username, password, config)) return authError("Identifiant ou mot de passe incorrect.", 401);
    const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(sessionCookieName(config), createSession(config), { httpOnly: true, secure: config.secure, sameSite: "strict", maxAge: SESSION_TTL_SECONDS, path: "/" });
    return response;
  } catch { return authError("La connexion est temporairement indisponible. Réessayez plus tard.", 503); }
}
