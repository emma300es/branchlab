# Build contract (v0.1)

The core generates a `Plan` (models.py) then runs declarative HTTP probes against immutable base/head exports. Providers never generate executable Python. Repository code still requires a sandbox; subprocess runner is explicit trusted-local-only. Demo provider is a labeled deterministic fixture, not a live AI result.

## Shared Python interfaces

`gitops.py`: `resolve_revision(repo: Path, ref: str) -> str`; `export_revision(repo: Path, sha: str, destination: Path) -> None`; `diff_context(repo: Path, base_sha: str, head_sha: str) -> dict` returning `diff` string, `changed_files` list[str], `files` mapping path -> {base: str, head: str}, `impact_graph` {nodes: [{id,label,kind,changed}], edges: [{source,target,kind}]}. Ignore binary/oversized/secret files in model context. Do not follow symlinks or allow path escapes.

`runner.py`: `run_probe(source_dir: Path, app_entry: str, probe: Probe, *, runner: str='docker', timeout: int=20, trusted_local: bool=False) -> dict`. Dict: `outcome` passed|failed|error|timeout, `duration_ms` number, `checks`: [{step_id,target,path,operator,expected,observed,passed}], `responses`: [{step_id,method,path,status,body}], `stdout` string, `stderr` string, `environment` object. Errors/import failures MUST NOT be assertion failures. Each run is a fresh process/container, controlled environment, no host API keys inherited. HTTP requests via FastAPI TestClient, no external network. Docker image `branchlab-runner:0.1`, built explicitly from docker/runner.Dockerfile. Subprocess rejects unless trusted_local=True. Timeout kills process group. App entry `service:app`. `worker.py` executes one serialized probe in subprocess/container, writes result JSON to a designated result file, not stdout. Worker should accept `--source PATH --app service:app --probe PATH --result PATH`. Use resource/time limits and bounded output.

`providers.py`: `build_plan(context: dict, pr_description: str, model: str|None=None) -> tuple[Plan,dict]` using OpenAI Responses `parse(text_format=Plan)`; returns provider {name,model,live_ai:true,input_tokens,output_tokens}. Read OPENAI_API_KEY only from env, do not log or save it. Model configurable via BRANCHLAB_MODEL default gpt-6-astra. `validate_citations(plan, context) -> list[dict]` returning per-probe validation {probe_id,valid,errors:[str]}; every citation must match exported revision/path/line range and quote. Zero citations invalid. Real context and PR body untrusted data, not tool instructions. `github.py`: `fetch_pr(url: str, destination: Path) -> dict` using existing `gh` CLI; GitHub-only URLs validated; clone base repository, fetch immutable PR base/head SHAs safely; return repo_path,base_ref,head_ref,pr_description,url. Read-only remote. No token printing or automatic comments. Write output only to new/empty destination.

## Evidence JSON dashboard schema

API read endpoints `/api/runs` -> list of full reports (newest first), `/api/runs/{id}` -> report, `/api/runs/{id}/report.md` and `/api/runs/{id}/report.json` downloadable. POST `/api/demo` runs bundled trusted deterministic demo and returns full report. It is the ONLY write endpoint; real repositories run via CLI. Bind API to localhost. UI proxy only forwards these allowed endpoints. Unknown/malformed IDs 404. Dashboard must not invent metrics.

Report fields:
```
{
 "schema_version":"1.0", "id":"run-...", "created_at":"UTC ISO",
 "repository":{"name":"tenant-cache","base_ref":"base","head_ref":"head","base_sha":"...","head_sha":"..."},
 "provider":{"name":"fixture"|"openai", "model":"deterministic-demo"|"...", "live_ai":false|true,"input_tokens":0,"output_tokens":0},
 "runner":"subprocess"|"docker", "repeats":2, "duration_ms":0,
 "intent_summary":"...", "changed_files":["service.py"],
 "impact_graph":{"nodes":[{"id":"service.py","label":"service.py","kind":"file","changed":true}],"edges":[{"source":"...","target":"...","kind":"imports"}]},
 "summary":{"suspected_regression":1,"intentional_change":1,"intent_unresolved":0,"no_regression":1,"flaky":0,"inconclusive":0,"invalid_baseline":0},
 "findings":[{
   "id":"tenant-isolation","title":"...","classification":"suspected_regression","severity":"high",
   "invariant":"...","intent":"preserve","rationale":"...","citations":[Citation],
   "citation_validation":{"valid":true,"errors":[]}, "steps":[Step],"assertions":[Assertion],
   "base_runs":[RunResult],"head_runs":[RunResult],
   "minimization":{"original_steps":3,"minimized_steps":2,"attempts":3,"status":"minimized"},
   "reproducer":"branchlab replay ..."
 }],
 "limitations":["..."],"reproduce_command":"..."
}
```

Classifications are conditional evidence, never proof a change is a bug. Preserve+valid citations+all base passes+all head assertion failures => suspected_regression. change+valid citations+same differential => intentional_change. Unknown/invalid citation => intent_unresolved. Mixed outcome runs => flaky. Any consistently failed import/runtime/timeout => inconclusive. Stable base assertion failure => invalid_baseline. Both pass => no_regression. Base fail/head pass => invalid_baseline (not new regression).

Frontend owns web/**. Runner agent owns gitops.py,runner.py,worker.py,docker/** and runner/git tests. Provider agent owns providers.py,github.py and provider/github tests. Parent owns models, engine, demo fixtures, CLI, API, integration tests, docs, CI, evaluation. Coordinate interface changes before editing shared files.
