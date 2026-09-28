# Architecture

## Packages

| Module | Responsibility |
| --- | --- |
| `gitops` | Resolve immutable commits, validate/export Git trees, bound source context and static imports |
| `github` | Read GitHub PR metadata, fetch exact objects and reject snapshot races |
| `github_app` | Signed webhooks, durable queue, installation-scoped fetch and Check Runs |
| `models` | Strict declarative plan schema and cross-reference validation |
| `providers` | Tool-free Responses/OpenClaw planning, optional trusted Codex adapter, exact citations |
| `runner` / `worker` | Fresh isolated executions, HTTP requests, typed checks, bounded results |
| `engine` | Repeat runs, classify observations, reduce failing sequences, persist evidence |
| `evaluate` | Named authored regression/control cases and measured fixture scorecard |
| `api` / `cli` | Authenticated evidence API, fixed demo, allowlisted PR queue and explicit investigations/replay |
| `web` | Next.js evidence workbench, owner sessions, PR submission and restricted same-origin proxy |

## Classification

Classification is deterministic **given a plan and observations**. The plan's intent remains a model/user interpretation:

1. No results: inconclusive.
2. Inconsistent observed outcomes: flaky/inconsistent.
3. Process errors, import errors, timeouts or malformed results: inconclusive.
4. Base assertions fail: invalid baseline; not evidence of a newly introduced regression.
5. Both sides pass: no regression observed by this probe.
6. Base passes/head fails, but citations are invalid or intent unknown: unresolved intent.
7. Base passes/head fails with grounded `change`: intentional change hypothesis.
8. Base passes/head fails with grounded `preserve`: suspected regression.

The words “suspected” and “observed” matter. Quoted repository text can be wrong, a PR can misstate its intention, and a passing test cannot cover behaviors it never exercised.

## Sequence reduction

The reducer greedily tries removing non-asserted setup steps, with a fixed attempt budget. Every accepted candidate must keep a stable base pass, head failure and the original failing assertion identities across repeated fresh executions. It preserves asserted steps and does not rewrite requests or invent new assertions. It is a bounded greedy reduction, not a guarantee of the globally smallest counterexample.

## Artifacts

Each run directory contains `plan.json`, `report.json` and `report.md`. The original plan is retained; findings hold the reduced sequence and measured paired traces. JSON is published last with an atomic rename so incomplete runs do not appear as successful dashboard results.

Reports record full base/head SHAs, planner provenance, execution mode, repeats, timeout, runtime durations, validated source references, typed outcomes and limitations. A replay makes no model call, uses the recorded revisions, and does not re-minimize its input.

## Deployment and App boundary

The HTTP service durably accepts signed, allowlisted PR metadata. A separate worker claims queue leases, resolves the same immutable PR snapshot through an installation-scoped token, plans and executes in Docker, and publishes a bounded Check Run. Crashed workers can be reclaimed; lease tokens fence late writers. Network delivery is not falsely called exactly once.

Remote dashboard requests require a signed owner session. The Next server forwards its own API bearer token; the token is never returned to the browser. The webhook uses an independent HMAC secret instead. Provider credentials remain in the trusted planner/controller and are absent from test containers. Compose's shared absolute temporary path is required for sibling Docker mounts.

No automatic fix generation, merge, full dependency resolver, semantic program proof, multi-user tenancy, or claimed benchmark against arbitrary repositories. Registration/installation and external DNS/TLS require operator-owned infrastructure and credentials; shipping the integration does not perform those actions implicitly.
