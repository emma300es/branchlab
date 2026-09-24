"use client";
import { useState } from "react";
import { classificationMeta, duration, formatValue, type Finding, type Report, type RunResult } from "@/lib/report";
import { Icon } from "./icons";

export function Badge({ classification }: { classification: Finding["classification"] }) {
  const info = classificationMeta[classification];
  return <span className={`badge ${info.tone}`}><span className="badge-dot" />{info.label}</span>;
}
function Outcome({ outcome }: { outcome: string }) {
  return <span className={`outcome outcome-${outcome}`}><Icon name={outcome === "passed" ? "check" : "warning"} width="13" height="13" />{outcome}</span>;
}
function Execution({ run, branch, sha }: { run?: RunResult; branch: string; sha: string }) {
  return <section className={`execution ${branch.toLowerCase()}`} aria-label={`${branch} execution`}>
    <header className="execution-head"><div><span className="eyebrow">{branch}</span><code>{sha.slice(0, 8)}</code></div>{run ? <Outcome outcome={run.outcome} /> : <span className="muted">Not recorded</span>}</header>
    {run && <>
      <div className="execution-meta"><Icon name="clock" width="13" height="13" /> {duration(run.duration_ms)} <span>fresh execution</span></div>
      {run.responses.length === 0 ? <p className="empty-small">No HTTP responses recorded.</p> : run.responses.map((response, i) => <div className="response" key={`${response.step_id}-${i}`}>
        <div className="response-head"><span className="step-index">{i + 1}</span><strong>{response.method}</strong><code>{response.path}</code><span className={`http-code ${response.status >= 400 ? "error" : ""}`}>{response.status}</span></div>
        <div className="response-label">{response.step_id} · response body</div>
        <pre>{formatValue(response.body)}</pre>
      </div>)}
      <div className="assertion-section"><h4>Assertion results <span>{run.checks.length}</span></h4>
        {run.checks.length === 0 && <p className="empty-small">No assertions completed.</p>}
        {run.checks.map((check, i) => <div className={`check-row ${check.passed ? "passed" : "failed"}`} key={i}>
          <div><Icon name={check.passed ? "check" : "close"} width="15" height="15"/><strong>{check.step_id}</strong><code>{check.target}{check.path ? `.${check.path}` : ""}</code><span>{check.passed ? "Pass" : "Fail"}</span></div>
          <dl><dt>Expected ({check.operator})</dt><dd><pre>{formatValue(check.expected)}</pre></dd><dt>Observed</dt><dd><pre>{formatValue(check.observed)}</pre></dd></dl>
        </div>)}
      </div>
      {!!run.stderr && <details className="log-detail"><summary>Execution stderr</summary><pre>{run.stderr}</pre></details>}
      {!!run.stdout && <details className="log-detail"><summary>Execution stdout</summary><pre>{run.stdout}</pre></details>}
      {run.environment && <details className="log-detail"><summary>Recorded environment</summary><pre>{formatValue(run.environment)}</pre></details>}
    </>}
  </section>;
}
function Citations({ finding }: { finding: Finding }) {
  return <div className="citations">
    <div className={`citation-status ${finding.citation_validation.valid ? "valid" : "invalid"}`}><Icon name={finding.citation_validation.valid ? "shield" : "warning"}/><div><strong>{finding.citation_validation.valid ? "Source citations validated" : "Source validation did not pass"}</strong><p>Validation checks the quoted text, revision and line range—not whether the model’s interpretation is correct.</p></div></div>
    {finding.citation_validation.errors.map((error, i) => <p className="inline-error" key={i}>{error}</p>)}
    {finding.citations.length === 0 && <p className="empty-small">No source citations were recorded.</p>}
    {finding.citations.map((citation, i) => <article className="citation-card" key={i}>
      <header><Icon name="file" width="16" height="16"/><strong>{String(citation.path ?? "Unknown source")}</strong><span>{String(citation.revision ?? "revision unavailable")}</span><code>L{String(citation.line_start ?? "?")}–{String(citation.line_end ?? "?")}</code></header>
      <pre>{String(citation.quote ?? "No quote recorded")}</pre>
    </article>)}
  </div>;
}
export function Impact({ report }: { report: Report }) {
  const nodes = report.impact_graph.nodes.slice(0, 24);
  const width = 760, rowHeight = 75;
  const left = nodes.filter(n => n.changed), right = nodes.filter(n => !n.changed);
  const height = Math.max(left.length, right.length, 1) * rowHeight + 42;
  const positions = new Map(nodes.map(n => [n.id, { x: n.changed ? 20 : 452, y: 24 + (n.changed ? left.indexOf(n) : right.indexOf(n)) * rowHeight }]));
  return <div className="impact-panel"><div className="section-intro"><h3>Change neighborhood</h3><p>Static relationships from the exported repository. Connections show code structure, not proven runtime causality.</p></div>
    {nodes.length > 0 ? <div className="graph-wrap"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Impact graph: ${report.impact_graph.nodes.length} nodes, ${report.impact_graph.edges.length} edges`}>
      <defs><marker id="edge-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8b9da7" /></marker></defs>
      {report.impact_graph.edges.map((e, i) => { const a = positions.get(e.source), b = positions.get(e.target); if (!a || !b) return null; const forward = a.x < b.x; const x1 = a.x + (forward ? 280 : 0), x2 = b.x + (forward ? 0 : 280); return <path key={i} d={`M${x1} ${a.y + 26} C${(x1 + x2) / 2} ${a.y + 26}, ${(x1 + x2) / 2} ${b.y + 26}, ${x2} ${b.y + 26}`} stroke="#9babb3" strokeWidth="1.5" fill="none" markerEnd="url(#edge-arrow)"><title>{e.source} → {e.target}: {e.kind}</title></path>; })}
      {nodes.map(n => { const p = positions.get(n.id)!; return <g key={n.id} transform={`translate(${p.x},${p.y})`}><title>{n.label} ({n.kind}, {n.changed ? "changed" : "unchanged"})</title><rect width="280" height="53" rx="8" fill={n.changed ? "#e2f6f0" : "#ffffff"} stroke={n.changed ? "#79bfa9" : "#d9e1e4"}/><circle cx="18" cy="26" r="4" fill={n.changed ? "#19876d" : "#84939c"}/><text x="32" y="24" fill="#23353e" fontSize="12" fontFamily="monospace">{n.label.length > 30 ? `${n.label.slice(0, 28)}…` : n.label}</text><text x="32" y="40" fill="#667984" fontSize="9" fontFamily="sans-serif">{n.kind.toUpperCase()} · {n.changed ? "CHANGED" : "UNCHANGED"}</text></g>; })}
    </svg></div> : <p className="empty-small">No graph nodes were recorded.</p>}
    {report.impact_graph.nodes.length > nodes.length && <p className="muted">Showing the first 24 nodes. Download the JSON report for the complete graph.</p>}
    <div className="graph-legend"><span><i className="changed"/> Changed source</span><span><i/> Unchanged context</span></div>
    <details className="log-detail"><summary>All recorded relationships ({report.impact_graph.edges.length})</summary><ul className="edge-list">{report.impact_graph.edges.map((edge, i) => <li key={i}><code>{edge.source}</code><span> {edge.kind} → </span><code>{edge.target}</code></li>)}</ul>{!report.impact_graph.edges.length && <p>No relationships recorded.</p>}</details>
  </div>;
}
function Probe({ finding }: { finding: Finding }) {
  return <div className="probe-plan"><div className="section-intro"><h3>Declarative HTTP probe</h3><p>Requests and assertions—not executable code generated by a model.</p></div>
    {finding.steps.map((step, i) => <div className="probe-step" key={i}><span className="step-index">{i + 1}</span><div><div><strong>{String(step.method ?? "Request")}</strong> <code>{String(step.path ?? "")}</code><span className="muted"> {String(step.id ?? "")}</span></div><details><summary>Request definition</summary><pre>{formatValue(step)}</pre></details></div></div>)}
    <h4>Assertions</h4><pre className="definition">{formatValue(finding.assertions)}</pre>
  </div>;
}
export function FindingDetail({ finding, report }: { finding: Finding; report: Report }) {
  const [tab, setTab] = useState("traces");
  const [repeat, setRepeat] = useState(0);
  const [copied, setCopied] = useState(false);
  const repeats = Math.max(finding.base_runs.length, finding.head_runs.length);
  const minimization = finding.minimization;
  async function copy() { try { await navigator.clipboard.writeText(finding.reproducer); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { setCopied(false); } }
  return <article className="finding-detail" aria-labelledby="finding-title">
    <div className="finding-heading"><div className="finding-labels"><Badge classification={finding.classification}/><span className={`severity severity-${finding.severity}`}>{finding.severity} severity</span></div><h2 id="finding-title">{finding.title}</h2><p>{finding.rationale}</p><div className="invariant"><span className="eyebrow">TESTED INVARIANT</span><p>{finding.invariant}</p></div></div>
    <div className={`evidence-note ${classificationMeta[finding.classification].tone}`}><Icon name="activity" width="17" height="17"/><span>{classificationMeta[finding.classification].description}</span></div>
    <nav className="evidence-tabs" aria-label="Evidence view">{[{ id: "traces", label: "Execution traces" }, { id: "citations", label: "Source citations" }, { id: "impact", label: "Impact map" }, { id: "probe", label: "Probe plan" }].map(item => <button key={item.id} type="button" aria-pressed={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}{item.id === "citations" && <span>{finding.citations.length}</span>}</button>)}</nav>
    {tab === "traces" && <div className="trace-panel"><div className="trace-toolbar"><div><h3>Same probe. Two revisions.</h3><p>Actual execution results, shown side by side.</p></div><label>Repeat <select value={Math.min(repeat, Math.max(0, repeats - 1))} onChange={e => setRepeat(Number(e.target.value))} aria-label="Execution repeat">{Array.from({ length: repeats }, (_, i) => <option key={i} value={i}>{i + 1} of {repeats}</option>)}</select></label></div><div className="execution-grid"><Execution run={finding.base_runs[repeat]} branch="BASE" sha={report.repository.base_sha}/><Execution run={finding.head_runs[repeat]} branch="HEAD" sha={report.repository.head_sha}/></div>
      <div className="repeat-strip"><span>ALL RECORDED RUNS</span><div><b>Base</b>{finding.base_runs.map((run, i) => <span key={i} className={`repeat-dot ${run.outcome}`} title={`Base repeat ${i + 1}: ${run.outcome}`} aria-label={`Base repeat ${i + 1}: ${run.outcome}`} />)}</div><div><b>Head</b>{finding.head_runs.map((run, i) => <span key={i} className={`repeat-dot ${run.outcome}`} title={`Head repeat ${i + 1}: ${run.outcome}`} aria-label={`Head repeat ${i + 1}: ${run.outcome}`} />)}</div></div>
    </div>}
    {tab === "citations" && <Citations finding={finding}/>}{tab === "impact" && <Impact report={report}/>}{tab === "probe" && <Probe finding={finding}/>}
    <footer className="finding-footer"><div className="minimize"><div className="mini-icon"><Icon name="layers"/></div><div><strong>Reproducer minimization</strong><p>{minimization.original_steps} → {minimization.minimized_steps} steps <span>·</span> {minimization.attempts} attempts <span>·</span> {minimization.status.replaceAll("_", " ")}</p></div></div><div className="command-box"><Icon name="terminal" width="17" height="17"/><code>{finding.reproducer || "No replay command recorded"}</code>{finding.reproducer && <button onClick={copy} className="icon-button" aria-label={copied ? "Command copied" : "Copy replay command"} title={copied ? "Copied" : "Copy command"}><Icon name={copied ? "check" : "copy"} width="16" height="16"/></button>}</div></footer>
  </article>;
}
