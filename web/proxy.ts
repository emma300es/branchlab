import { NextRequest, NextResponse } from "next/server";
import { authConfig, requestOrigin, validSession } from "./lib/auth";

export function proxy(request: NextRequest) {
  let config;
  try { config = authConfig(); }
  catch { return new NextResponse("Dashboard configuration is incomplete.", { status: 503, headers: { "Cache-Control": "no-store" } }); }
  if (!requestOrigin(request.url, request.headers.get("host"), config)) return new NextResponse("Unexpected dashboard host.", { status: 403 });
  if (!config.remote) return NextResponse.next();
  const path = request.nextUrl.pathname;
  if (path === "/login" || path === "/api/session" || path.startsWith("/_next/static/") || path === "/favicon.ico") return NextResponse.next();
  if (!validSession(request.cookies.get(config.cookieName)?.value, config)) {
    if (path.startsWith("/api/")) return NextResponse.json({ error: "Sign in to continue." }, { status: 401, headers: { "Cache-Control": "no-store" } });
    return NextResponse.redirect(new URL("/login", config.origin!), { headers: { "Cache-Control": "no-store" } });
  }
  return NextResponse.next();
}
export const config = { matcher: ["/:path*"] };
