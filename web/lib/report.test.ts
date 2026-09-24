import assert from "node:assert/strict";
import { test } from "node:test";
import { classificationMeta, filterFindings, isReport, providerLabel, type Finding } from "./report";
const finding = { id: "tenant-isolation", title: "Cache crosses tenant boundaries", classification: "suspected_regression", severity: "high", invariant: "Tenant isolation", intent: "preserve", rationale: "A cited invariant", citations: [{ path: "service.py", line_start: 1, line_end: 1, revision: "base", quote: "tenants" }], citation_validation: { valid: true, errors: [] }, steps: [], assertions: [], base_runs: [{ outcome: "passed", duration_ms: 10, checks: [], responses: [] }], head_runs: [{ outcome: "failed", duration_ms: 12, checks: [], responses: [] }], minimization: { original_steps: 2, minimized_steps: 2, attempts: 1, status: "unchanged" }, reproducer: "branchlab replay" } as Finding;
const report = { schema_version: "1.0", id: "run-20260924-110000-abcdef12", created_at: "2026-09-24T11:00:00Z", repository: { name: "example", base_ref: "base", head_ref: "head", base_sha: "1234567", head_sha: "abcdef0" }, provider: { name: "fixture", model: "deterministic-demo", live_ai: false }, runner: "subprocess", repeats: 2, duration_ms: 100, intent_summary: "Preserve tenant isolation", changed_files: ["service.py"], impact_graph: { nodes: [], edges: [] }, summary: Object.fromEntries(Object.keys(classificationMeta).map(k => [k, k === "suspected_regression" ? 1 : 0])), findings: [finding], limitations: [], reproduce_command: "branchlab replay" };
test("supported evidence is accepted without requiring fabricated model usage", () => { assert.equal(isReport(report), true); assert.equal(isReport({ ...report, provider: { ...report.provider, input_tokens: null, output_tokens: null } }), true); });
test("missing counts, unknown classification and incomplete executions fail closed", () => {
  assert.equal(isReport({ ...report, summary: {} }), false);
  assert.equal(isReport({ ...report, findings: [{ ...finding, classification: "definitely-a-bug" }] }), false);
  assert.equal(isReport({ ...report, findings: [{ ...finding, base_runs: [{ outcome: "passed" }] }] }), false);
  assert.equal(isReport({ ...report, schema_version: "9.0" }), false);
  assert.equal(isReport({ ...report, findings: [{ ...finding, classification: "constructor" }] }), false);
  assert.equal(isReport({ ...report, findings: [{ ...finding, base_runs: [{ outcome: "passed", duration_ms: 1, responses: [{ path: {} }], checks: [] }] }] }), false);
});
test("finding search and classification filters compose without changing source evidence", () => {
  const pass = { ...finding, id: "health", title: "Health check", invariant: "Responds OK", classification: "no_regression" } as Finding;
  const list = [finding, pass];
  assert.deepEqual(filterFindings(list, "all", " TENANT "), [finding]);
  assert.deepEqual(filterFindings(list, "no_regression", "tenant"), []);
  assert.deepEqual(filterFindings(list, "no_regression", ""), [pass]);
  assert.equal(list.length, 2);
});
test("fixture, saved plan and live planner identities remain distinct", () => {
  assert.equal(providerLabel(report.provider), "Deterministic fixture plan");
  assert.equal(providerLabel({ ...report.provider, name: "replay" }), "Replayed evidence plan");
  assert.equal(providerLabel({ ...report.provider, name: "supplied-plan" }), "Supplied probe plan");
  assert.equal(providerLabel({ name: "openai", model: "selected-model", live_ai: true }), "Live AI plan");
});
