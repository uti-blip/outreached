import { type NextRequest, NextResponse } from "next/server";
import { authorize } from "@/lib/auth";
import { sessionCookieName } from "@/lib/auth-core";

export const runtime = "nodejs";
export async function POST(request: NextRequest) {
  const auth = authorize(request);
  if (auth instanceof NextResponse) return auth;
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store", "Clear-Site-Data": '"cache"' } });
  response.cookies.set(sessionCookieName(auth.config), "", { httpOnly: true, secure: auth.config.secure, sameSite: "strict", maxAge: 0, path: "/" });
  return response;
}
