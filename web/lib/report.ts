export const classificationMeta = {
  suspected_regression: { label: "Suspected regression", short: "Regression", tone: "danger", description: "The baseline passed and the head failed repeatedly against a cited, preserved invariant. Review the evidence before calling it a bug." },
  intentional_change: { label: "Intentional change", short: "Intentional", tone: "purple", description: "The baseline passed and the head failed, but the cited intent explicitly describes a behavior change." },
  intent_unresolved: { label: "Intent unresolved", short: "Unresolved", tone: "warning", description: "A behavioral difference was observed, but its relationship to intended behavior could not be established." },
  no_regression: { label: "No regression observed", short: "No regression", tone: "success", description: "Both revisions passed these probes. This is evidence for the tested behavior, not a guarantee about all behavior." },
  flaky: { label: "Inconsistent results", short: "Flaky", tone: "warning", description: "Repeated executions disagreed. A stable regression conclusion is not supported." },
  inconclusive: { label: "Inconclusive", short: "Inconclusive", tone: "neutral", description: "Execution errors or timeouts prevented a reliable comparison. These are not assertion failures." },
  invalid_baseline: { label: "Invalid baseline", short: "Baseline", tone: "neutral", description: "The baseline did not pass reliably. This does not establish a new regression in the head." },
} as const;
export type Classification = keyof typeof classificationMeta;
export interface RunResult {
  outcome: "passed" | "failed" | "error" | "timeout";
  duration_ms: number;
  checks: { step_id: string; target: string; path: string; operator: string; expected: unknown; observed: unknown; passed: boolean }[];
  responses: { step_id: string; method: string; path: string; status: number; body: unknown }[];
  stdout?: string; stderr?: string; environment?: Record<string, unknown>;
}
export interface Finding {
  id: string; title: string; classification: Classification; severity: string; invariant: string;
  intent: string; rationale: string; citations: Record<string, unknown>[];
  citation_validation: { valid: boolean; errors: string[] };
  steps: Record<string, unknown>[]; assertions: Record<string, unknown>[];
  base_runs: RunResult[]; head_runs: RunResult[];
  minimization: { original_steps: number; minimized_steps: number; attempts: number; status: string };
  reproducer: string;
}
export interface Report {
  schema_version: string; id: string; created_at: string;
  repository: { name: string; base_ref: string; head_ref: string; base_sha: string; head_sha: string };
  provider: { name: string; model: string; live_ai: boolean; input_tokens?: number | null; output_tokens?: number | null };
  runner: string; repeats: number; duration_ms: number; intent_summary: string;
  changed_files: string[];
  impact_graph: { nodes: { id: string; label: string; kind: string; changed: boolean }[]; edges: { source: string; target: string; kind: string }[] };
  summary: Record<Classification, number>;
  findings: Finding[]; limitations: string[]; reproduce_command: string;
}
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === "string");
const nonnegative = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const runs = (v: unknown): boolean => Array.isArray(v) && v.every(x => record(x) && ["passed", "failed", "error", "timeout"].includes(String(x.outcome)) && nonnegative(x.duration_ms)
  && Array.isArray(x.responses) && x.responses.every(r => record(r) && [r.step_id, r.method, r.path].every(s => typeof s === "string") && nonnegative(r.status))
  && Array.isArray(x.checks) && x.checks.every(c => record(c) && [c.step_id, c.target, c.path, c.operator].every(s => typeof s === "string") && typeof c.passed === "boolean")
  && (x.stdout === undefined || typeof x.stdout === "string") && (x.stderr === undefined || typeof x.stderr === "string"));

/** Reject incomplete or unsupported reports instead of displaying invented defaults. */
export function isReport(v: unknown): v is Report {
  if (!record(v) || v.schema_version !== "1.0" || typeof v.id !== "string" || !/^run-[a-zA-Z0-9_-]{1,96}$/.test(v.id) || typeof v.created_at !== "string") return false;
  if (!record(v.repository) || !["name", "base_ref", "head_ref", "base_sha", "head_sha"].every(k => typeof v.repository === "object" && v.repository !== null && typeof (v.repository as Record<string, unknown>)[k] === "string")) return false;
  if (!record(v.provider) || typeof v.provider.live_ai !== "boolean" || typeof v.provider.name !== "string" || typeof v.provider.model !== "string") return false;
  if (typeof v.runner !== "string" || typeof v.intent_summary !== "string" || typeof v.reproduce_command !== "string" || !nonnegative(v.repeats) || !nonnegative(v.duration_ms)) return false;
  if (!strings(v.changed_files) || !strings(v.limitations) || !record(v.summary) || !Object.keys(classificationMeta).every(k => nonnegative((v.summary as Record<string, unknown>)[k]))) return false;
  if (!record(v.impact_graph) || !Array.isArray(v.impact_graph.nodes) || !Array.isArray(v.impact_graph.edges)) return false;
  if (!v.impact_graph.nodes.every(n => record(n) && typeof n.id === "string" && typeof n.label === "string" && typeof n.kind === "string" && typeof n.changed === "boolean")) return false;
  if (!v.impact_graph.edges.every(e => record(e) && [e.source, e.target, e.kind].every(x => typeof x === "string"))) return false;
  return Array.isArray(v.findings) && v.findings.every(f => record(f) && ["id", "title", "severity", "invariant", "intent", "rationale", "reproducer"].every(k => typeof f[k] === "string") && typeof f.classification === "string" && Object.hasOwn(classificationMeta, f.classification) && Array.isArray(f.citations) && f.citations.every(record) && record(f.citation_validation) && typeof f.citation_validation.valid === "boolean" && strings(f.citation_validation.errors) && Array.isArray(f.steps) && f.steps.every(record) && Array.isArray(f.assertions) && f.assertions.every(record) && runs(f.base_runs) && runs(f.head_runs) && record(f.minimization) && typeof f.minimization.status === "string" && [f.minimization.original_steps, f.minimization.minimized_steps, f.minimization.attempts].every(nonnegative));
}
export function filterFindings(findings: Finding[], filter: string, query: string): Finding[] {
  const term = query.trim().toLowerCase();
  return findings.filter(f => (filter === "all" || f.classification === filter) && (!term || `${f.title} ${f.invariant} ${f.id}`.toLowerCase().includes(term)));
}
export function formatValue(value: unknown): string { return value === undefined ? "Not recorded" : typeof value === "string" ? value : JSON.stringify(value, null, 2); }
export function duration(ms: number): string { return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`; }

export function providerLabel(provider: Report["provider"]): string {
  if (provider.live_ai) return "Live AI plan";
  if (provider.name === "fixture") return "Deterministic fixture plan";
  if (provider.name === "replay") return "Replayed evidence plan";
  return "Supplied probe plan";
}
