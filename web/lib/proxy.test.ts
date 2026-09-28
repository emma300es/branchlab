import assert from "node:assert/strict";
import { test } from "node:test";
import { investigationBody, permittedPath, sameOriginPost, upstreamUrl } from "./proxy";
const run = "run-20260924-110000-abcdef12";
test("allowlist permits only evidence reads and bundled demo writes", () => {
  assert.equal(permittedPath("GET", ["runs"]), "/api/runs");
  assert.equal(permittedPath("GET", ["runs", run]), `/api/runs/${run}`);
  assert.equal(permittedPath("GET", ["runs", run, "report.json"]), `/api/runs/${run}/report.json`);
  assert.equal(permittedPath("GET", ["runs", run, "report.md"]), `/api/runs/${run}/report.md`);
  assert.equal(permittedPath("POST", ["demo"]), "/api/demo");
  for (const path of [["runs", ".."], ["runs", "run-../../secrets"], ["runs", "run-invalid"], ["runs", run, "plan.json"], ["runs", run, "report.json", "extra"], ["health"], ["http://evil.example"], ["demo"]]) assert.equal(permittedPath("GET", path), null);
  assert.equal(permittedPath("POST", ["runs"]), null);
  assert.equal(permittedPath("DELETE", ["runs", run]), null);
});
test("PR investigations and integer job reads have narrow capabilities", () => {
  assert.equal(permittedPath("POST", ["investigations"]), "/api/investigations");
  assert.equal(permittedPath("GET", ["jobs"]), "/api/jobs");
  assert.equal(permittedPath("GET", ["jobs", "123"]), "/api/jobs/123");
  for (const id of ["-1", "0", "01", "1.5", "1e2", "../../../secret", "9999999999999999"]) assert.equal(permittedPath("GET", ["jobs", id]), null);
  assert.equal(permittedPath("POST", ["github", "webhook"]), null);
  assert.equal(permittedPath("GET", ["jobs", "1", "logs"]), null);
  assert.equal(investigationBody({ pr_url: "https://github.com/owner/repo/pull/123" }), '{"pr_url":"https://github.com/owner/repo/pull/123"}');
  assert.equal(investigationBody({ pr_url: "https://github.com/owner/../pull/123" }), null);
  for (const value of [null, [], { pr_url: "https://github.com/owner/repo/pull/1", runner: "subprocess" }, { pr_url: "file:///etc/passwd" }, { pr_url: "https://github.com/owner/repo/pull/0" }, { pr_url: "https://user:secret@github.com/owner/repo/pull/1" }, { pr_url: "https://github.com/owner/repo/pull/1?x=secret" }]) assert.equal(investigationBody(value), null);
});
test("POST requires exact same local origin and JSON, not cross-origin forms or DNS rebinding", () => {
  assert.equal(sameOriginPost("http://localhost:3000/api/demo", "http://localhost:3000", "application/json; charset=utf-8"), true);
  assert.equal(sameOriginPost("http://127.0.0.1:3000/api/demo", "http://127.0.0.1:3000", "application/json"), true);
  for (const origin of [null, "null", "http://evil.example", "http://localhost:9999"]) assert.equal(sameOriginPost("http://localhost:3000/api/demo", origin, "application/json"), false);
  assert.equal(sameOriginPost("http://evil.example:3000/api/demo", "http://evil.example:3000", "application/json"), false);
  assert.equal(sameOriginPost("http://localhost:3000/api/demo", "http://localhost:3000", "text/plain"), false);
});
test("upstream is a fixed configured origin without credential or path injection", () => {
  assert.equal(upstreamUrl("http://127.0.0.1:8765", "/api/runs").href, "http://127.0.0.1:8765/api/runs");
  for (const base of ["file:///tmp", "http://user:secret@host", "http://host/path", "http://host/?key=secret", "http://host/#anchor"]) assert.throws(() => upstreamUrl(base, "/api/runs"));
});
