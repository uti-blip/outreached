import { type NextRequest, NextResponse } from "next/server";
import { authorize } from "@/lib/auth";
import { csrfToken } from "@/lib/auth-core";

export const runtime = "nodejs";
export async function GET(request: NextRequest) {
  const auth = authorize(request);
  if (auth instanceof NextResponse) return auth;
  return NextResponse.json({ csrfToken: csrfToken(auth.token, auth.config) }, { headers: { "Cache-Control": "no-store", "Vary": "Cookie" } });
}
