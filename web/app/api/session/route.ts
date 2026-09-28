import { NextRequest, NextResponse } from "next/server";
import { authConfig, createSession, loginLimiter, sameOriginJson, SESSION_SECONDS, tokenMatches } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  let config;
  try { config = authConfig(); } catch { return NextResponse.json({ error: "Authentication is not configured." }, { status: 503 }); }
  if (!config.remote || !sameOriginJson(request, config)) return NextResponse.json({ error: "Request not permitted." }, { status: 403 });
  if (!loginLimiter.allow()) return NextResponse.json({ error: "Too many attempts. Try again in one minute." }, { status: 429, headers: { "Retry-After": "60", "Cache-Control": "no-store" } });
  const raw = await boundedBody(request);
  let token: unknown;
  try { const body = JSON.parse(raw); token = Object.keys(body).length === 1 ? body.token : undefined; } catch { /* Invalid input shares the authentication failure response. */ }
  if (typeof token !== "string" || token.length > 4096 || !tokenMatches(token, config.ownerToken)) return NextResponse.json({ error: "Access token not recognized." }, { status: 401, headers: { "Cache-Control": "no-store" } });
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(config.cookieName, createSession(config), { httpOnly: true, secure: config.secure, sameSite: "strict", path: "/", maxAge: SESSION_SECONDS });
  return response;
}
export async function DELETE(request: NextRequest) {
  let config;
  try { config = authConfig(); } catch { return NextResponse.json({ error: "Authentication is not configured." }, { status: 503 }); }
  if (!config.remote || !sameOriginJson(request, config)) return NextResponse.json({ error: "Request not permitted." }, { status: 403 });
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(config.cookieName, "", { httpOnly: true, secure: config.secure, sameSite: "strict", path: "/", maxAge: 0 });
  return response;
}
async function boundedBody(request: Request): Promise<string> {
  const reader = request.body?.getReader();
  if (!reader) return "";
  const parts: Uint8Array[] = []; let bytes = 0;
  for (;;) {
    const chunk = await reader.read(); if (chunk.done) break;
    bytes += chunk.value.byteLength;
    if (bytes > 8192) { await reader.cancel(); return ""; }
    parts.push(chunk.value);
  }
  return Buffer.concat(parts).toString("utf8");
}
