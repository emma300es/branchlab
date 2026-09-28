# Trust boundaries

## What is executed

Model output is a validated `Plan`: local HTTP paths, methods, headers, JSON request bodies and assertions. It is not generated Python or shell. However, **the application under test is executable repository code**. Declarative probes do not make that application safe.

Docker is the default for non-demo investigations: no network, read-only root and source/input mounts, non-root uid, dropped capabilities, no-new-privileges, bounded memory/CPU/PIDs, and a disposable output area. The runner does not mount provider or GitHub credentials, the Docker socket, or the host home directory. It does not automatically install dependencies or pull/build images.

Containers share a kernel and can have vulnerabilities. Use a disposable, isolated runner host/VM for actively hostile code. The tested process contains the application and test client; adversarial application code could tamper with test results. BranchLab is an engineering investigation tool, not a cryptographic attestation service.

`--runner subprocess --trust-local-code` is an explicit opt-in for code you trust. Environment filtering, timeouts and resource limits reduce accidents; they do not prevent network access, host file access or dishonest results. It is never an automatic fallback when Docker is unavailable. The HTTP demo endpoint runs only the authored fixture, not caller-provided code.

## Data flow

The planner receives bounded source excerpts, diffs, a static import graph and the PR description. File-name pattern filtering excludes common secret locations, but it is not comprehensive DLP. Review `branchlab inspect` output before sending a sensitive repository to any external model.

The Responses adapter supplies no model tools, disables response storage in its request, and uses locally supplied credentials. Provider errors are sanitized. Source/PR contents are data, not instructions. These controls reduce prompt-injection impact; they do not establish that AI hypotheses or intent labels are correct.

The optional Codex adapter is only for explicitly trusted context. It uses an empty temporary working directory, ignores user configuration/project instruction files, restricts features and rejects observed tool calls. CLI versions may have capabilities outside those switches. Therefore it is **not** offered as an equivalent no-tools isolation boundary for untrusted PR text.

## Local services and evidence

The API binds loopback by default, validates Host headers, restricts artifact paths and rejects symlinked reports. Hosted mode requires an explicit HTTPS origin and server bearer token. The Next.js dashboard requires a strong owner token, uses signed expiring HttpOnly/SameSite sessions, checks exact origins for mutations and forwards its own server token only to the fixed backend. It provides no arbitrary repository path or command endpoint. PR submission is limited to installation/repository IDs in operator configuration.

GitHub webhooks have a separate raw-body HMAC boundary and body limit; they only enqueue durable metadata. Work occurs in a separate trusted controller. The Compose worker has Docker socket access, equivalent to privileged control of that dedicated host. Test containers never inherit the socket or provider/App credentials. Use a dedicated worker host rather than a shared machine containing unrelated accounts.

Reports contain responses, source quotes, paths and probe inputs. They are intentionally useful evidence and may contain sensitive application data. `.branchlab/`, `.env` files and local evidence are ignored by Git. GitHub Actions artifacts from the sample workflow contain only authored fixture data; adapt retention and access controls before adding private repository evidence.

## GitHub permissions

PR ingestion reads through `gh` and Git. It does not create comments, reviews, commits, approvals or merges in the target repository. It captures and verifies immutable revisions, rejects ambiguous URLs and mid-fetch PR updates, and disables hooks and automatic checkout. GitHub Actions uses `contents: read`; it does not use `pull_request_target` or execute external PR code with privileged credentials.

The optional App mode instead uses short-lived installation tokens scoped to allowed repository IDs with contents/pull-requests read and checks write. It publishes Check Runs, not comments or approvals. Webhook-provided clone URLs, executable settings, tokens and app entry points are never trusted. PR metadata is rechecked around fetch and before reporting. Provider errors are sanitized; check summaries omit response bodies, source quotes and host paths.

## Reporting

For a security issue, contact the repository owner privately. Do not place tokens, private source, sensitive traces or exploit credentials in a public issue.
