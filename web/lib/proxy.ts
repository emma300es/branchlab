const RUN_ID = /^run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/;

/** A deliberately small capability boundary; this is not a generic HTTP proxy. */
export function permittedPath(method: string, segments: string[]): string | null {
  if (method === "POST") return segments.length === 1 && segments[0] === "demo" ? "/api/demo" : null;
  if (method !== "GET" || segments[0] !== "runs") return null;
  if (segments.length === 1) return "/api/runs";
  if (!RUN_ID.test(segments[1] ?? "")) return null;
  if (segments.length === 2) return `/api/runs/${segments[1]}`;
  if (segments.length === 3 && ["report.md", "report.json"].includes(segments[2])) {
    return `/api/runs/${segments[1]}/${segments[2]}`;
  }
  return null;
}

export function upstreamUrl(base: string, path: string): URL {
  const url = new URL(base);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("BRANCHLAB_API_URL must be an HTTP(S) origin without credentials, a path, query, or fragment.");
  }
  return new URL(path, url.origin);
}

export function sameOriginPost(requestUrl: string, origin: string | null, contentType: string | null): boolean {
  const url = new URL(requestUrl);
  return ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) && origin === url.origin && contentType?.split(";")[0].trim() === "application/json";
}
