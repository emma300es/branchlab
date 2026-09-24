import { permittedPath, sameOriginPost, upstreamUrl } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: Context) {
  const { path } = await context.params;
  const host = request.headers.get("host") ?? "";
  if (!/^(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$/.test(host)) return Response.json({ error: "Local dashboard host required." }, { status: 403 });
  // Next may normalize request.url to localhost even when the client used 127.0.0.1.
  // Use the validated Host authority, not forwarded headers, for the origin check.
  const localRequestUrl = new URL(request.url);
  localRequestUrl.host = host;
  const permitted = permittedPath(request.method, path);
  if (!permitted || new URL(request.url).search) return Response.json({ error: "Route not found." }, { status: 404 });
  if (request.method === "POST" && !sameOriginPost(localRequestUrl.href, request.headers.get("origin"), request.headers.get("content-type"))) {
    return Response.json({ error: "A same-origin JSON request is required." }, { status: 403 });
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
      ...(request.method === "POST" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
    });
    if (!upstream.ok) {
      // Do not leak internal paths, exception text or configuration through the proxy.
      return Response.json({ error: upstream.status === 404 ? "Run not found." : "The evidence server could not complete this request." }, { status: upstream.status === 404 ? 404 : 502 });
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
    return new Response(Buffer.concat(chunks), { status: 200, headers });
  } catch {
    return Response.json({ error: "Evidence server unavailable or timed out. Start the BranchLab API and retry." }, { status: 503 });
  }
}
export const GET = proxy;
export const POST = proxy;
