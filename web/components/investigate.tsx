"use client";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./icons";

type Job = { job_id: number; status: "queued" | "running" | "completed" | "failed" | "skipped"; repository: string; pr_number: number; report_id: string | null };
const runId = /^run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$/;
function isJob(value: unknown): value is Job {
  if (!value || typeof value !== "object") return false;
  const job = value as Job;
  return Number.isSafeInteger(job.job_id) && job.job_id > 0 && ["queued", "running", "completed", "failed", "skipped"].includes(job.status) && typeof job.repository === "string" && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(job.repository) && Number.isSafeInteger(job.pr_number) && job.pr_number > 0 && (job.report_id == null || runId.test(job.report_id));
}
export default function Investigate({ refreshReports, selectReport }: { refreshReports: () => Promise<void>; selectReport: (id: string) => void }) {
  const [url, setUrl] = useState(""); const [jobs, setJobs] = useState<Job[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [message, setMessage] = useState("");
  const statuses = useRef(new Map<number, string>());
  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/jobs", { cache: "no-store" });
      if (!response.ok) return; // Local report viewing also works without an App worker.
      const data: unknown = await response.json();
      if (!Array.isArray(data) || !data.every(isJob)) return;
      if (data.some(job => job.status === "completed" && statuses.current.has(job.job_id) && statuses.current.get(job.job_id) !== "completed")) await refreshReports();
      statuses.current = new Map(data.map(job => [job.job_id, job.status]));
      setJobs(data.slice(0, 20));
    } catch { /* The primary dashboard owns connection reporting. */ }
  }, [refreshReports]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!jobs.some(job => ["queued", "running"].includes(job.status))) return;
    const timer = setInterval(() => void load(), 5000); return () => clearInterval(timer);
  }, [jobs, load]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch("/api/investigations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pr_url: url.trim() }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "The investigation could not be started.");
      if (!Number.isSafeInteger(data.job_id) || data.job_id < 1) throw new Error("The server returned an invalid job.");
      setMessage(data.duplicate ? "This pull request is already tracked. Its latest status is below." : "Investigation queued. Results will appear here when ready.");
      setUrl(""); await load();
    } catch (e) { setError(e instanceof Error ? e.message : "The investigation could not be started."); }
    finally { setBusy(false); }
  }
  return <section className="investigate-panel" aria-labelledby="investigate-title"><div><div className="eyebrow teal">FROM PULL REQUEST TO PROOF</div><h2 id="investigate-title">Investigate a GitHub PR</h2><p>Use a pull request from a repository connected to your GitHub App.</p></div><form onSubmit={submit}><label className="sr-only" htmlFor="pr-url">GitHub pull-request URL</label><input type="url" id="pr-url" required maxLength={250} placeholder="https://github.com/owner/repo/pull/123" value={url} onChange={event => setUrl(event.target.value)}/><button className="button primary" disabled={busy} type="submit"><Icon name="branch" width="15"/>{busy ? "Starting…" : "Investigate PR"}</button></form>{error && <p role="alert" className="login-error">{error}</p>}{message && <p role="status" className="job-message">{message}</p>}{jobs.length > 0 && <div className="job-list" aria-label="Investigation jobs">{jobs.slice(0, 5).map(job => <div className="job-row" key={job.job_id}><a href={`https://github.com/${job.repository}/pull/${job.pr_number}`} rel="noreferrer" target="_blank">{job.repository}<strong>#{job.pr_number}</strong></a><span className={`job-status ${job.status}`}>{job.status}</span>{job.report_id && job.status === "completed" ? <button className="text-button" onClick={async () => { await refreshReports(); selectReport(job.report_id!); }}>View evidence</button> : job.status === "failed" ? <small>See worker logs for the failure.</small> : null}</div>)}</div>}</section>;
}
