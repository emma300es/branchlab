# Architecture

## Packages

| Module | Responsibility |
| --- | --- |
| `gitops` | Resolve immutable commits, validate/export Git trees, bound source context and static imports |
| `github` | Read GitHub PR metadata, fetch exact objects and reject snapshot races |
| `models` | Strict declarative plan schema and cross-reference validation |
| `providers` | Tool-free Responses structured planning, optional trusted Codex adapter, exact citations |
| `runner` / `worker` | Fresh isolated executions, HTTP requests, typed checks, bounded results |
| `engine` | Repeat runs, classify observations, reduce failing sequences, persist evidence |
| `evaluate` | Named authored regression/control cases and measured fixture scorecard |
| `api` / `cli` | Local evidence API, fixed demo trigger and explicit investigations/replay |
| `web` | Next.js evidence workbench with restricted same-origin proxy |

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

## Deliberate first-release boundaries

No queue service, webhook receiver, GitHub App installation, automatic fix generation, automatic merge, full dependency resolver, semantic program proof, multi-user deployment, or claimed benchmark against arbitrary repositories. These require more than adding another agent role. The first release concentrates on a testable evidence pipeline end to end.
