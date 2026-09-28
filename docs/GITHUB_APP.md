# GitHub App: signed PR investigations

BranchLab v0.2 includes a real GitHub App receiver and persistent worker, not a
simulated webhook. It still needs **your App registration, installation, private
key and webhook secret**, a reachable HTTPS webhook endpoint, Docker, and a
working model connection. Source code being implemented and tested does **not**
mean an App has been installed or a production webhook has been delivered.

## Minimal privileges and operator-owned scope

Start with [github-app-manifest.json](github-app-manifest.json), replacing the
placeholder webhook hostname and choosing an available App name. Register a
private GitHub App and install it only on repositories you intend to investigate.
The receiver is `POST /api/github/webhook`. Subscribe to `pull_request`; grant
Contents **read**, Pull requests **read**, Checks **write**, and the implicit
Metadata **read** permission. No issue-comment, workflow, administration or
content-write permission is needed. This version neither commits nor merges.

Keep the App's RSA private key in a protected local file and configure secrets in
your service environment or secret manager, **not in source files, URLs or chat**.
Choose a randomly generated webhook secret of at least 32 bytes. The App ID is
not its client ID; the installation ID is not a user ID. Obtain repository IDs
from GitHub's repository metadata, not from a webhook you have not verified.

Required environment (values below are placeholders):

```dotenv
BRANCHLAB_GITHUB_APP_ID=123456
BRANCHLAB_GITHUB_APP_PRIVATE_KEY_FILE=/run/secrets/branchlab-app.pem
BRANCHLAB_GITHUB_WEBHOOK_SECRET=<random-secret-from-your-secret-manager>
BRANCHLAB_GITHUB_ALLOWLIST_JSON=[{"installation_id":123,"repository_id":456,"full_name":"owner/repository","app_entry":"service:app"}]
BRANCHLAB_GITHUB_STATE_DIR=/var/lib/branchlab/github
BRANCHLAB_DATA_DIR=/var/lib/branchlab/runs
BRANCHLAB_PLANNER_PROVIDER=openai
BRANCHLAB_MODEL=gpt-6-astra
OPENAI_API_KEY=<from-your-secret-manager>
```

Allowlist entries bind the **installation ID, immutable repository ID, and full
name together**. A repository rename, transfer or reinstallation fails closed
until the operator reviews the mapping. Each trusted entry selects the FastAPI
`module:attribute`; a PR cannot choose an executable, Docker image, host path,
model or runner. Only the installed base repository is a fetch destination;
fork URLs and clone URLs from event payloads are never used.

`BRANCHLAB_PLANNER_PROVIDER=openclaw` is also available for a host-native worker
with the OpenClaw CLI and an existing authenticated provider. It uses the lean
`infer model run` completion path, **not a chat-agent turn or tool-enabled Codex
session**. Do not mount a broad agent credential directory into an untrusted
repository container. Standard standalone/Docker deployments use `openai`.

## Receiver and worker are separate processes

The HTTP route verifies the exact body bytes against `X-Hub-Signature-256` using
HMAC-SHA256 and a constant-time comparison. It bounds payloads to 1 MB, validates
installation/repository IDs and immutable 40-character commit SHAs, and accepts
`opened`, `synchronize`, `reopened`, and `ready_for_review` for non-draft open PRs.
Other event types/actions are ignored. Invalid signatures cannot queue work.

An accepted job is committed to SQLite **before** the 202 response. The HTTP
handler never runs code, a model, git, or Docker. Delivery IDs and snapshot
fingerprints deduplicate repeated requests, including repeats after a restart.
Reusing a delivery ID with different content is rejected. At most 500 queued or
running jobs are admitted; a full queue returns 503 rather than acknowledging
lost work. Keep SQLite, its WAL, snapshots and reports on durable storage.

Backend integration contract:

```python
from branchlab.github_app import AppConfig, JobQueue, create_webhook_router, AppWorker

config = AppConfig.from_env()
queue = JobQueue(config.state_dir / "queue.sqlite3")
app.include_router(create_webhook_router(config, queue))

# In a separate trusted worker process; schedule or loop with an idle sleep.
worker = AppWorker(config, queue)
result = worker.run_once()  # one job, or None when idle
```

The App module's router is HMAC-authenticated, not browser-cookie or bearer
authenticated. Your reverse proxy must preserve request bytes and signature
headers, cap request sizes, and forward only the webhook path publicly. Do not
expose private reports or owner submission endpoints without their deployment
authentication. TLS and request-rate limits belong at the deployment boundary.

For an authenticated owner submission endpoint,
`enqueue_pr_url(pr_url, config, queue)` reads current GitHub metadata with an
installation token and enqueues the same validated snapshot. It rejects URLs
outside the configured repositories. A manual resubmission retries a failed job;
it does not duplicate a queued/running/completed snapshot. The App module itself
does not create an unauthenticated manual submission route.

`queue.list_jobs(limit=50)` and `queue.get(job_id)` return public status fields:
`job_id`, `status`, `attempts`, `repository`, `pr_number`, `base_sha`, `head_sha`,
`report_id`, `error`, `created_at`, `updated_at`. Timestamps are epoch seconds;
states are `queued`, `running`, `completed`, `failed`, `skipped`. Queue internals,
secrets and raw PR descriptions are not exposed.

## Execution and publication boundaries

1. The worker rechecks the operator allowlist, mints a ten-minute-or-shorter RSA
   signed App JWT, and requests a short-lived installation token restricted to
   the one configured repository and minimal permissions. It never uses `gh`
   user authentication. Tokens are not persisted or put into git arguments.
2. It rereads PR identity, SHAs, draft/open state, and description before fetching.
   It uses a fresh **bare** git repository, disabled credential helpers/hooks,
   no recursive submodules, no file/ext transport, no redirects, and exact SHAs.
   No PR file is checked out or executed on the host. Authentication is provided
   only to the git subprocess through a temporary fixed-code askpass helper.
3. It rereads metadata after fetching. Any SHA, repository identity or description
   race causes a skipped stale job; the worker never silently switches commits.
4. Source context is bounded and secret-filtered before the tool-free planner.
   Plans are declarative HTTP requests/assertions, not generated executable code.
   App jobs select at most four probes with at most five requests each. Each
   side runs twice with a ten-second per-run timeout; reduction work is bounded
   by the probe length. Longer plans are explicitly out of scope for this App
   execution budget. Selected/omitted counts and reasons are saved with the
   report and exposed in the Check Run; omissions are not silently hidden.
   No dependencies from a PR are installed on the host.
5. Investigated code runs only through the existing Docker runner with no
   network, read-only source/root filesystem, dropped capabilities, non-root
   UID, and memory/CPU/process limits. The worker does **not** fall back to
   subprocess when Docker or the operator-built runner image is missing.
   Additional application dependencies must be reviewed and installed in that
   trusted image ahead of time; a PR cannot choose its own build instructions.
6. The worker saves the full private report and rechecks PR metadata before
   reporting. A stale run is never presented as a result for newer changes.
7. A Check Run on the exact head SHA reports safe counts, classifications and
   escaped source-path/line references. It omits raw source, request bodies,
   stdout/stderr, raw model text and host filesystem paths. Full traces and
   replay commands stay in your private dashboard. Suspected regressions set
   `failure`; unresolved/inconclusive/empty cases are `action_required` (not the
   required-check-passing `neutral` result); `success` means only
   no regression observed by these probes, not a proof of correctness.

The default runtime image is `branchlab-runner:0.1` (the existing runner protocol,
not an App release-number guarantee). Build it using `docker/runner.Dockerfile`
before processing real jobs. Docker isolation reduces exposure; it is not a
claim that arbitrary hostile code is safe on a sensitive multi-tenant host. Use
a dedicated disposable worker host for untrusted external PRs.

## Restart and failure behavior

Workers claim jobs transactionally and refresh a two-minute lease every 30
seconds. A killed worker's job becomes claimable after lease expiry, up to three
crash attempts. Lease tokens fence stale queue writers. Stored Check Run IDs and
the stable GitHub `external_id` allow recovery if the process dies after creating
a check but before recording its ID. Private report IDs are checkpointed before
delivery so a completed report can be reused after an interrupted publication.

This is **at-least-once execution after crashes**, not exactly-once distributed
execution. An abrupt crash can repeat model work or produce a duplicate private
report before its checkpoint. The bounded GitHub recovery query searches the
latest 100 matching checks on that SHA; pathological histories can still require
operator reconciliation. Ordinary duplicate deliveries do not create extra jobs.

Authentication/model/runtime failures become `failed`, never a fabricated clean
result. Errors exposed through the queue/check are fixed safe messages; raw
provider/network exceptions are not stored. Investigations may retain a report
even if Check Run delivery fails; the error explicitly marks unconfirmed
delivery. Fix the configuration and manually resubmit to retry. Closed or changed
PR snapshots are `skipped`; a newer synchronize event supplies a new job.

Queue records, replay snapshots and reports are retained for reproducibility.
Set an operator retention/disk-quota policy for your deployment; no unattended
cleanup deletes historical evidence in this release.

## What is verified

`tests/test_github_app.py` exercises real ASGI ingress, signatures, bounds,
forged IDs/refs, delivery replay, durable queue recovery/fencing, RSA JWT
verification, repository-scoped token requests, redirect rejection, immutable
fetch race detection, credential isolation, forced Docker/tool-free execution,
stale-result handling and escaped Check Run summaries. GitHub HTTP requests are
mocked in those tests; tests do not claim a live App installation or webhook
delivery. Consult the build's verification receipt for separately performed
live checks and remaining deployment blockers.
