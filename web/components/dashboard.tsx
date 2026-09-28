"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { classificationMeta, duration, filterFindings, isReport, providerLabel, type Classification, type Report } from "@/lib/report";
import { Badge, FindingDetail, Impact } from "./evidence";
import { Icon, type IconName } from "./icons";
import Investigate from "./investigate";

type View = "evidence" | "impact" | "about";
function date(value: string) { const d = new Date(value); return Number.isNaN(d.valueOf()) ? "Date unavailable" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }

export default function Dashboard({ remote = false }: { remote?: boolean }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [findingId, setFindingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("evidence");
  const report = reports.find(r => r.id === selected) ?? reports[0];
  const findings = useMemo(() => filterFindings(report?.findings ?? [], filter, query), [report, filter, query]);
  const finding = findings.find(f => f.id === findingId) ?? findings[0];

  const refresh = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const response = await fetch("/api/runs", { cache: "no-store" });
      if (response.status === 401) { window.location.replace("/login"); return; }
      if (!response.ok) throw new Error("Cannot reach the evidence server. Check that the API is running, then retry.");
      const data: unknown = await response.json();
      if (!Array.isArray(data) || !data.every(isReport)) throw new Error("The server returned an unsupported or incomplete report. No inferred results are shown.");
      setReports(data); setConnected(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load investigations."); setConnected(false); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function demo() {
    setRunning(true); setError(null);
    try {
      const response = await fetch("/api/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const data: unknown = await response.json();
      if (!response.ok) throw new Error(typeof data === "object" && data && "error" in data ? String(data.error) : "The demo could not complete. Check the evidence server.");
      if (!isReport(data)) throw new Error("The demo returned an incomplete report. Inspect the backend output before using these results.");
      setReports(previous => [data, ...previous.filter(r => r.id !== data.id)]); setSelected(data.id); setFindingId(null); setFilter("all"); setQuery(""); setConnected(true); setView("evidence");
    } catch (e) { setError(e instanceof Error ? e.message : "The demo failed."); }
    finally { setRunning(false); }
  }
  function selectReport(id: string) { setSelected(id); setFindingId(null); setFilter("all"); setQuery(""); setView("evidence"); }
  async function logout() {
    const response = await fetch("/api/session", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (response.ok) window.location.replace("/login"); else setError("Could not sign out. Please retry.");
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to evidence</a>
    <aside className="sidebar" aria-label="Workspace navigation">
      <a className="brand" href="/"><span className="brand-icon"><Icon name="branch" width="25" height="25"/></span><span>branch<span className="brand-light">lab</span><small>CHANGE INTELLIGENCE</small></span></a>
      <div className="workspace-switch"><span className="workspace-avatar">{remote ? "P" : "L"}</span><div><strong>{remote ? "Private workspace" : "Local workspace"}</strong><small>{remote ? "Owner-authenticated access" : "Evidence stays on your machine"}</small></div><span className="workspace-dot"/></div>
      <div className="nav-label">WORKSPACE</div>
      <nav className="main-nav">{([{ id: "evidence", icon: "grid", label: "Investigations" }, { id: "impact", icon: "branch", label: "Impact explorer" }, { id: "about", icon: "flask", label: "How it works" }] as { id: View; icon: IconName; label: string }[]).map(item => <button key={item.id} onClick={() => setView(item.id)} aria-current={view === item.id ? "page" : undefined} className={view === item.id ? "selected" : ""}><Icon name={item.icon}/>{item.label}{item.id === "evidence" && <span className="nav-count">{reports.length}</span>}</button>)}</nav>
      <div className="recent-label"><span className="nav-label">RECENT RUNS</span><span>{reports.length ? String(reports.length).padStart(2, "0") : "—"}</span></div>
      <div className="recent-runs">{reports.slice(0, 8).map(r => <button key={r.id} className={`recent-run ${report?.id === r.id ? "active" : ""}`} onClick={() => selectReport(r.id)}><span className={`run-dot ${r.summary.suspected_regression ? "danger" : "success"}`}/><span><strong>{r.repository.name}</strong><small>{date(r.created_at)}</small></span><Icon name="chevron" width="12" height="12"/></button>)}{reports.length === 0 && <p className="recent-empty">Your investigations will appear here.</p>}</div>
      <div className="sidebar-bottom"><div className="version"><span className="tiny-brand"><Icon name="flask" width="17" height="17"/></span><span>Built to show its work.<small>BranchLab · v0.2</small></span></div><span className="local-mode"><span/> {remote ? "PRIVATE WORKSPACE" : "LOCAL MODE"}</span></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb">Workspace <Icon name="chevron" width="13" height="13"/><strong>{view === "evidence" ? "Investigations" : view === "impact" ? "Impact explorer" : "How it works"}</strong></div><div className="topbar-actions"><span className={`connection ${connected ? "online" : "offline"}`}><i/>{loading ? "Connecting" : connected ? "API connected" : "API unavailable"}</span><button type="button" className="icon-button" onClick={refresh} disabled={loading || running} aria-label="Refresh investigations"><Icon name="refresh" className={loading ? "spin" : ""} width="17" height="17"/></button>{remote && <button className="text-button" onClick={logout}>Sign out</button>}<span className="owner-avatar" title={remote ? "Private workspace" : "Local workspace"}>BL</span></div></header>
      <main id="main-content" className="main-content">
        <section className="page-heading"><div><div className="eyebrow teal">EVIDENCE BEFORE CONFIDENCE</div><h1>{view === "about" ? "From a diff to a defensible finding." : view === "impact" ? "See beyond the changed lines." : "Every change tells a story."}</h1><p>{view === "evidence" ? "Understand what changed. Test what should not." : view === "impact" ? "Trace the source relationships behind this investigation." : "AI proposes the questions. Executions supply the evidence."}</p></div><button className="button primary" onClick={demo} disabled={running || loading}><Icon name={running ? "refresh" : "play"} className={running ? "spin" : ""} width="16" height="16"/>{running ? "Investigating demo…" : "Run local demo"}</button></section>
        {error && <div className="error-banner" role="alert"><Icon name="warning"/><div><strong>Evidence unavailable</strong><p>{error}{reports.length > 0 && " Previously loaded evidence is still shown; it may be stale."}</p></div><button type="button" className="button secondary" disabled={loading || running} onClick={refresh}>Retry</button></div>}
        {view === "evidence" && <Investigate refreshReports={refresh} selectReport={selectReport}/>}
        {running && <div className="running-banner" role="status"><span className="loading-orbit"/><div><strong>Running the trusted local fixture</strong><p>Creating real Git revisions, executing repeated HTTP probes and comparing results. Usually completes within a minute.</p></div></div>}
        {view === "about" ? <About/> : loading && !report ? <Loading/> : !report ? <Empty onDemo={demo} running={running}/> : <>
          <section className="run-overview"><div className="repo-row"><div className="repo-name"><span className="repo-icon"><Icon name="branch" width="22" height="22"/></span><div><div className="eyebrow">SELECTED INVESTIGATION</div><h2>{report.repository.name}</h2></div></div><div className="run-downloads"><a className="button subtle" href={`/api/runs/${encodeURIComponent(report.id)}/report.json`} download><Icon name="download" width="15" height="15"/>JSON</a><a className="button secondary" href={`/api/runs/${encodeURIComponent(report.id)}/report.md`} download><Icon name="download" width="15" height="15"/>Export report</a></div></div>
            <div className="run-meta"><span className="revision"><span>BASE</span><code>{report.repository.base_ref}</code><small>{report.repository.base_sha.slice(0, 7)}</small></span><Icon name="arrow" width="17" height="17"/><span className="revision head"><span>HEAD</span><code>{report.repository.head_ref}</code><small>{report.repository.head_sha.slice(0, 7)}</small></span><span className="meta-divider"/><span><Icon name="clock" width="14" height="14"/>{duration(report.duration_ms)}</span><span>{report.repeats} repeats</span><span>{report.changed_files.length} changed files</span><time dateTime={report.created_at}>{date(report.created_at)}</time></div>
            <div className={`provider-notice ${report.provider.live_ai ? "live" : "fixture"}`}><Icon name="flask" width="16" height="16"/><strong>{providerLabel(report.provider)}</strong><span>{report.provider.live_ai ? report.provider.model : "Not a live AI result"}</span><span className="provider-runner">{report.runner === "subprocess" ? "Trusted-local process" : report.runner} runner</span></div>
            <div className="metrics"><Metric label="Suspected regressions" value={report.summary.suspected_regression} tone="danger" icon="warning" note="Repeated behavioral differences"/><Metric label="Intentional changes" value={report.summary.intentional_change} tone="purple" icon="branch" note="Explained by cited intent"/><Metric label="No regression observed" value={report.summary.no_regression} tone="success" icon="check" note="Probes passed on both revisions"/><Metric label="Needs review" value={report.summary.intent_unresolved + report.summary.flaky + report.summary.inconclusive + report.summary.invalid_baseline} tone="neutral" icon="search" note="Unresolved or unreliable evidence"/></div>
          </section>
          {reports.length > 8 && <label className="all-runs-select">Browse all investigations <select value={report.id} onChange={e => selectReport(e.target.value)}>{reports.map(r => <option key={r.id} value={r.id}>{r.repository.name} — {date(r.created_at)}</option>)}</select></label>}
          {view === "impact" ? <section className="standalone-impact"><Impact report={report}/><div className="changed-files"><h3>Changed files</h3>{report.changed_files.map(file => <code key={file}><Icon name="file" width="14" height="14"/>{file}</code>)}</div></section> : <>
            <div className="section-title"><h2>Finding workbench <span>{report.findings.length}</span></h2><span>FOLLOW THE EVIDENCE</span></div>
            <div className="workbench"><aside className="findings-panel" aria-label="Finding selection"><div className="finding-search"><Icon name="search" width="16" height="16"/><input placeholder="Search findings…" aria-label="Search findings" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button className="icon-button" aria-label="Clear search" onClick={() => setQuery("")}><Icon name="close" width="14" height="14"/></button>}</div><label className="filter-label">Classification<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All findings ({report.findings.length})</option>{Object.entries(classificationMeta).map(([key, info]) => <option key={key} value={key}>{info.short} ({report.summary[key as Classification]})</option>)}</select></label><div className="finding-list">{findings.map((f, i) => <button key={f.id} type="button" className={`finding-card ${finding?.id === f.id ? "active" : ""}`} onClick={() => setFindingId(f.id)} aria-pressed={finding?.id === f.id}><div><span className="finding-number">{String(i + 1).padStart(2, "0")}</span><span className={`severity-dot ${f.severity}`}>{f.severity}</span></div><h3>{f.title}</h3><Badge classification={f.classification}/><p>{f.invariant}</p>{finding?.id === f.id && <Icon name="arrow" className="selected-arrow" width="16" height="16"/>}</button>)}</div>{!findings.length && <div className="no-findings"><Icon name="search"/><h3>No matching findings</h3><p>Try another classification or search term.</p><button className="text-button" onClick={() => { setFilter("all"); setQuery(""); }}>Clear filters</button></div>}<div className="findings-footnote"><Icon name="shield" width="15" height="15"/><p>Findings are conditional evidence, not proof that a change is a bug.</p></div></aside>{finding ? <FindingDetail key={`${report.id}:${finding.id}`} finding={finding} report={report}/> : <div className="empty-detail"><Icon name="file" width="35" height="35"/><p>Select a finding to inspect its evidence.</p></div>}</div>
          </>}
          <section className="intent-summary"><div className="section-title"><h2>Intent & scope</h2><span>WHAT THIS RUN CAN TELL YOU</span></div><p>{report.intent_summary}</p>{report.limitations.length > 0 && <details open><summary>Recorded limitations ({report.limitations.length})</summary><ul>{report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}<div className="run-id"><code>{report.id}</code><span>Schema {report.schema_version} · {report.provider.name}</span></div></section>
        </>}
        <footer className="page-footer"><span>BRANCHLAB</span><p>Hypotheses from models. Evidence from execution.</p><span>LOCAL-FIRST / EVIDENCE DRIVEN</span></footer>
      </main>
    </div>
  </div>;
}
function Metric({ label, value, tone, icon, note }: { label: string; value: number; tone: string; icon: IconName; note: string }) { return <div className={`metric metric-${tone}`}><div><span>{label}</span><Icon name={icon} width="16" height="16"/></div><strong>{value}</strong><small>{note}</small></div>; }
function Loading() { return <section className="loading-state" role="status"><div className="loading-orbit"/><h2>Connecting to your evidence</h2><p>Reading completed investigations from the evidence API.</p></section>; }
function Empty({ onDemo, running }: { onDemo: () => void; running: boolean }) {
  return <section className="empty-state"><div className="empty-visual" aria-hidden="true"><div className="empty-node base"><Icon name="branch"/><span>base</span><i/></div><div className="visual-line"/><div className="empty-center"><Icon name="flask" width="33" height="33"/></div><div className="visual-line"/><div className="empty-node head"><Icon name="branch"/><span>head</span><i/></div></div><span className="eyebrow teal">YOUR FIRST INVESTIGATION</span><h2>Two branches. One question.<br/><span>What actually changed?</span></h2><p>BranchLab turns a code diff into testable hypotheses,<br className="desktop-break"/> then compares real behavior across both revisions.</p><button className="button primary large" onClick={onDemo} disabled={running}><Icon name={running ? "refresh" : "play"} width="17" height="17" className={running ? "spin" : ""}/>{running ? "Running investigation…" : "Investigate the demo"}<Icon name="arrow" width="18" height="18"/></button><small className="demo-disclaimer">Trusted bundled code · deterministic planner · no model key needed</small><div className="empty-process">{[{ n: "01", icon: "branch", title: "Snapshot", text: "Immutable base & head" }, { n: "02", icon: "flask", title: "Probe", text: "Cited behavior hypotheses" }, { n: "03", icon: "activity", title: "Compare", text: "Repeated, fresh executions" }, { n: "04", icon: "file", title: "Inspect", text: "Traces & minimal repros" }].map(item => <div key={item.n}><span>{item.n}</span><Icon name={item.icon as IconName}/><strong>{item.title}</strong><small>{item.text}</small></div>)}</div><div className="empty-cli"><Icon name="terminal"/><div><strong>Bring your own repository</strong><p>Run investigations through the CLI. Start with <code>branchlab --help</code> to choose revisions and a runner.</p></div></div></section>;
}
function About() { return <section className="about-panel"><div className="about-lead"><span className="eyebrow teal">THE INVESTIGATION LOOP</span><h2>Intelligence is useful.<br/>Inspectable evidence is better.</h2><p>A plausible explanation is not a verified regression. BranchLab separates the planner’s hypotheses from the results of running them.</p></div><div className="about-steps">{[{ title: "Pin the change", text: "Export immutable base and head revisions. Build bounded diff context and a source relationship graph.", icon: "branch" }, { title: "Ask concrete questions", text: "A planner proposes declarative HTTP probes and assertions with exact source citations. It never emits executable test code.", icon: "flask" }, { title: "Test both realities", text: "Repeat probes against both revisions in fresh executions, using Docker or the explicitly trusted-local runner. Keep assertion failures separate from runtime errors and timeouts.", icon: "activity" }, { title: "Show the reasoning trail", text: "Inspect the actual requests, responses, citations and repeat outcomes. Minimize stable regression reproducers and export the report.", icon: "file" }].map((s, i) => <article key={s.title}><span className="about-number">0{i + 1}</span><Icon name={s.icon as IconName}/><h3>{s.title}</h3><p>{s.text}</p></article>)}</div><div className="trust-note"><Icon name="shield"/><div><h3>A deliberate boundary</h3><p>The dashboard can investigate pull requests from installed, explicitly allowed GitHub App repositories and display their evidence. Real repository probes use Docker; the bundled demo uses trusted fixture code. The subprocess runner is for trusted code only; use the Docker runner for isolated repository execution. Neither route makes untrusted code universally safe.</p><p>Fixture plans are always labeled. No demo fixture is presented as a live AI result.</p></div></div></section>; }
