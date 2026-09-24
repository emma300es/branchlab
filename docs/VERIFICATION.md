# Verification

This document distinguishes checks of real execution from mocks and untested integrations.

## Build-time checks

- Python unit/integration tests execute real Git exports, FastAPI probes, repeated base/head comparisons, minimization and evidence API reads.
- Provider adapter tests exercise the actual OpenAI SDK with a mock HTTP transport. They test parsing, schema/refusal/error handling and usage reporting; they do not prove account access.
- GitHub adapter unit tests validate URLs, command boundaries, immutable snapshot metadata and race handling. Separate read-only smoke evidence is recorded if performed.
- Frontend tests cover data/proxy behavior; type checking and production build check the dashboard bundle.
- The included CI builds the Docker worker and runs the authored demo inside it. Runtime results are available in GitHub Actions; constructing the correct Docker command alone is not a sandbox execution test.

## Live-model limitation in this build environment

No `OPENAI_API_KEY` was configured. A trusted-fixture Codex planner attempt reached the standalone CLI but returned HTTP 401. No live AI plan was produced, and no credential/login settings were changed to force access. The adapter remains usable with separately configured provider access. All fixture reports are marked `live_ai: false`.

## Evaluation scope

The fixture evaluation compares actual measured classifications with expected outcomes on a small authored corpus. It is a pipeline regression suite, not evidence of general model accuracy or coverage across arbitrary applications. Failure to run a case is a failed/inconclusive check, not silently counted as success.

## Reproduce

```sh
pytest -q
ruff check src tests
branchlab evaluate --output .branchlab/evaluation
docker build -f docker/runner.Dockerfile -t branchlab-runner:0.1 .
branchlab demo --runner docker
cd web && npm test && npm run typecheck && npm run build
```

See the repository's CI run and downloadable artifacts for exact commit-specific results. Local dependency versions are recorded in `requirements.lock` and `web/package-lock.json`; the Docker base tag is not a digest-pinned, bit-reproducible supply chain.
