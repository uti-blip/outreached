import "server-only";
import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";
import { allowedOrigin, authConfig, sessionCookieName, verifyCsrf, verifySession, type AuthConfig } from "./auth-core";

export function authError(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
}
export function getAuthConfig() { return authConfig(process.env); }

export async function hasPageSession(): Promise<boolean> {
  const config = getAuthConfig();
  return Boolean(config && verifySession((await cookies()).get(sessionCookieName(config))?.value, config));
}

export function authorize(request: NextRequest): { config: AuthConfig; token: string } | NextResponse {
  const config = getAuthConfig();
  if (!config) return authError("Cet espace est temporairement indisponible. Contactez son administrateur.", 503);
  const token = request.cookies.get(sessionCookieName(config))?.value;
  if (!token || !verifySession(token, config)) return authError("Votre session a expiré. Connectez-vous à nouveau.", 401);
  const mutation = !["GET", "HEAD", "OPTIONS"].includes(request.method);
  if (!allowedOrigin(request, config, mutation) || (mutation && !verifyCsrf(token, request.headers.get("x-csrf-token"), config))) return authError("Requête non autorisée. Rechargez la page et réessayez.", 403);
  return { config, token };
}

export async function readLimitedBody(request: Request, maxBytes: number): Promise<Buffer | null> {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > maxBytes)) return null;
  if (!request.body) return Buffer.alloc(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  return Buffer.concat(chunks);
}
