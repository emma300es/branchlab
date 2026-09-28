import { investigationBody, permittedPath, upstreamUrl } from "@/lib/proxy";
import { authConfig, requestOrigin, sameOriginJson, validSession } from "@/lib/auth";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
  const { path } = await context.params;
  let config;
  try { config = authConfig(); } catch { return Response.json({ error: "Dashboard configuration is incomplete." }, { status: 503 }); }
  if (!requestOrigin(request.url, request.headers.get("host"), config)) return Response.json({ error: "Unexpected dashboard host." }, { status: 403 });
  if (config.remote && !validSession(request.cookies.get(config.cookieName)?.value, config)) return Response.json({ error: "Sign in to continue." }, { status: 401 });
  const permitted = permittedPath(request.method, path);
  if (!permitted || new URL(request.url).search) return Response.json({ error: "Route not found." }, { status: 404 });
  if (request.method === "POST" && !sameOriginJson(request, config)) {
    return Response.json({ error: "A same-origin JSON request is required." }, { status: 403 });
  }
  let body = "{}";
  if (request.method === "POST") {
    const reader = request.body?.getReader(); const chunks: Uint8Array[] = []; let bytes = 0;
    if (reader) for (;;) {
      const chunk = await reader.read(); if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > 2048) { await reader.cancel(); return Response.json({ error: "Request is too large." }, { status: 413 }); }
      chunks.push(chunk.value);
    }
    try {
      const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (path[0] === "investigations") {
        const validated = investigationBody(parsed);
        if (!validated) throw new Error("Invalid request");
        body = validated;
      } else if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed) || Object.keys(parsed).length !== 0) throw new Error("Invalid demo body");
    } catch { return Response.json({ error: "Provide a GitHub pull-request URL, with no extra settings." }, { status: 400 }); }
  }
  let destination: URL;
  try { destination = upstreamUrl(process.env.BRANCHLAB_API_URL ?? "http://127.0.0.1:8765", permitted); }
  catch { return Response.json({ error: "The backend URL is misconfigured." }, { status: 503 }); }
  try {
    const upstream = await fetch(destination, {
      method: request.method,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(request.method === "POST" ? 120_000 : 15_000),
      headers: { ...(config.apiToken ? { Authorization: `Bearer ${config.apiToken}` } : {}), ...(request.method === "POST" ? { "Content-Type": "application/json" } : {}) },
      ...(request.method === "POST" ? { body } : {}),
    });
    if (!upstream.ok) {
      // Do not leak internal paths, exception text or configuration through the proxy.
      const publicError = upstream.status === 404 ? "Evidence not found." : path[0] === "investigations" ? "Investigation unavailable. Check the GitHub App installation, allowed repository, and model setup." : "The evidence server could not complete this request.";
      return Response.json({ error: publicError }, { status: [400, 403, 404, 409, 429, 503].includes(upstream.status) ? upstream.status : 502 });
    }
    const reader = upstream.body?.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > MAX_RESPONSE_BYTES) { await reader.cancel(); throw new Error("Oversized report"); }
        chunks.push(value);
      }
    }
    const headers: Record<string, string> = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Type": path.at(-1) === "report.md" ? "text/markdown; charset=utf-8" : "application/json; charset=utf-8" };
    if (["report.md", "report.json"].includes(path.at(-1) ?? "")) headers["Content-Disposition"] = `attachment; filename="${path[1]}.${path.at(-1) === "report.md" ? "md" : "json"}"`;
    return new Response(Buffer.concat(chunks), { status: upstream.status, headers });
  } catch {
    return Response.json({ error: "Evidence server unavailable or timed out. Start the BranchLab API and retry." }, { status: 503 });
  }
}
export const GET = proxy;
export const POST = proxy;
