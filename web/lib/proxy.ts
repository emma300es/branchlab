const RUN_ID = /^run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/;

/** A deliberately small capability boundary; this is not a generic HTTP proxy. */
export function permittedPath(method: string, segments: string[]): string | null {
  if (method === "POST") return segments.length === 1 && ["demo", "investigations"].includes(segments[0]) ? `/api/${segments[0]}` : null;
  if (method === "GET" && segments[0] === "jobs") {
    if (segments.length === 1) return "/api/jobs";
    if (segments.length === 2 && /^[1-9][0-9]{0,14}$/.test(segments[1])) return `/api/jobs/${segments[1]}`;
    return null;
  }
  if (method !== "GET" || segments[0] !== "runs") return null;
  if (segments.length === 1) return "/api/runs";
  if (!RUN_ID.test(segments[1] ?? "")) return null;
  if (segments.length === 2) return `/api/runs/${segments[1]}`;
  if (segments.length === 3 && ["report.md", "report.json"].includes(segments[2])) {
    return `/api/runs/${segments[1]}/${segments[2]}`;
  }
  return null;
}

export function investigationBody(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).length !== 1 || !("pr_url" in value) || typeof value.pr_url !== "string") return null;
  if (!/^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}\/pull\/[1-9][0-9]{0,9}$/.test(value.pr_url)) return null;
  if (new URL(value.pr_url).href !== value.pr_url) return null;
  return JSON.stringify({ pr_url: value.pr_url });
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
