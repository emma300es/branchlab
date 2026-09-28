# BranchLab private deployment

BranchLab supports a **single-owner, authenticated HTTPS workspace**. The supplied
Compose deployment runs a standalone Next.js dashboard, evidence API, Caddy TLS
terminator, and optional durable GitHub App worker. Local CLI/demo use remains
available without deploying anything.

## Deployment inputs

On a Docker host with DNS pointed at it, copy `deploy/environment.example` to
`.env.deploy` and configure
these values through a local secret manager or protected environment file:

| Variable | Purpose |
| --- | --- |
| `BRANCHLAB_HOST` | DNS hostname, without scheme or path |
| `BRANCHLAB_PUBLIC_ORIGIN` | Exact `https://hostname` origin; no trailing slash, path, query, or credentials |
| `BRANCHLAB_OWNER_TOKEN` | Random owner login token, at least 32 characters |
| `BRANCHLAB_SESSION_SECRET` | Independent random HMAC signing secret, at least 32 characters |
| `BRANCHLAB_API_TOKEN` | Independent random server-to-server bearer token, at least 32 characters |

Use three distinct randomly generated secrets, not human passwords. Neither
secrets nor access tokens belong in URLs, Git commits, PR comments, browser
JavaScript configuration, or `NEXT_PUBLIC_*` variables. No secret is supplied by
the repository. The login screen accepts the owner token over HTTPS; the other
two secrets stay on the server.

Keep `.env.deploy` readable only by the deployment operator. The dashboard and API
fail closed for incomplete remote authentication settings. The default local
mode accepts only loopback Host authorities; binding that mode to a network
interface does not make it a supported public deployment.

## Start the workspace

From the repository root:

```sh
cp deploy/environment.example .env.deploy
chmod 600 .env.deploy
# Populate .env.deploy before running the commands below.
docker compose --env-file .env.deploy config --quiet
docker compose --env-file .env.deploy up --build -d api web https
docker compose --env-file .env.deploy ps
```

For an initial **no-App** workspace, leave `BRANCHLAB_GITHUB_APP_ID` and
`BRANCHLAB_GITHUB_APP_PRIVATE_KEY_FILE` blank in `.env.deploy`. Create the empty
placeholder file used by the Compose mount, so Docker does not create a directory
in its place:

```sh
mkdir -p .branchlab
touch .branchlab/unconfigured-app.pem
```

Do this before `compose up`. This is only a mount placeholder; it is not an App
credential, and App features remain unavailable until properly configured.

Caddy acquires the TLS certificate using `BRANCHLAB_HOST`; ports 80 and 443 must
be reachable and DNS must match. No API or Next.js port is published directly.
Caddy preserves the original Host header. BranchLab ignores client-supplied
forwarded-host headers when deciding which dashboard origin is allowed.

Open `BRANCHLAB_PUBLIC_ORIGIN`, enter the configured **owner access token**, and
run the bundled demo. This performs real base/head executions using authored
fixture probes, explicitly labeled **not a live AI result**. Reports persist in
the `evidence` volume. Clicking Sign out clears the browser session.

When the App is unconfigured, the dashboard still displays existing reports and
runs the demo. Submitting an actual PR returns a configuration error; it never
silently falls back to invented findings.

## Enable real PR investigations

Configure the GitHub App, installation allowlist, repository entrypoint, and
model access according to [GitHub App setup](GITHUB_APP.md). The App's private key
is a read-only secret-file mount, not a browser credential. Keep its webhook
secret separate from the three dashboard secrets.

The Compose worker needs a prebuilt probe image, Docker access, and a host
workspace mounted at the **same absolute path** inside and outside its container:

```sh
docker build -f docker/runner.Dockerfile -t branchlab-runner:0.1 .
docker compose --env-file .env.deploy --profile github up --build -d
```

Set `BRANCHLAB_WORKSPACE` to a writable, operator-controlled host directory and
`DOCKER_GID` to the socket's actual group ID. Before starting the worker, create
that workspace and make it writable by the container's UID/GID **10001:10001**.
The App private-key file must be readable by UID 10001 through its read-only
mount; keep it inaccessible to other host users (for example, operator-managed
ownership or an ACL instead of making it world-readable). Use a dedicated runner host/VM:
Docker socket access grants substantial host authority. Repository probes run in
networkless, restricted containers; the worker itself holds the App/model
credentials and is part of the trusted control plane. The supplied generic probe
image supports the documented FastAPI stack. Additional dependencies require a
reviewed custom runner image, not automatic installation from an untrusted PR.

Once the App is installed and the worker configured, either:

- paste an allowed GitHub PR URL into **Investigate a GitHub PR**, or
- let signed `pull_request` webhooks enqueue an investigation.

The dashboard shows queued/running/completed/failed/skipped jobs and links completed
jobs to normal evidence reports. Submitting a duplicate is handled by the backend
queue rather than starting an extra execution. Polling occurs while work is
queued/running. The UI exposes no repository filesystem paths, shell commands,
runner overrides, model overrides, or arbitrary upstream URLs.

## HTTP boundaries

- All remote evidence pages, jobs, reports, and downloads require an owner session.
- Login is globally limited to five attempts per minute **per dashboard process**.
  It uses fixed-length timing-safe credential comparison. This is a single-replica
  deployment; scaling replicas requires a shared limiter/session strategy.
- Sessions are HMAC-signed, origin-bound, expire after eight hours, and use
  `__Host-branchlab-session; Secure; HttpOnly; SameSite=Strict; Path=/` on HTTPS.
- Session cookies are bearer credentials. Sign out clears the current browser's
  cookie; rotate `BRANCHLAB_SESSION_SECRET` and restart the web service to revoke
  **all** sessions (including copied cookies). Rotating only the login token does
  not revoke already-issued sessions.
- State-changing dashboard requests require the exact configured Origin and
  JSON. The dashboard forwards only its configured API bearer token, not client
  authorization headers, browser cookies, or the owner token.
- `/api/github/webhook` routes directly to the backend and uses its own raw-body
  HMAC verification. It is not an unauthenticated evidence endpoint or a generic
  dashboard proxy route.
- The API health endpoint contains only service/version status. Responses are
  marked `no-store`; dashboards do not embed secret configuration in HTML.

## Verify and operate

```sh
cd web
npm ci
npm test
npm run typecheck
npm run build
npm run smoke:auth
```

The auth smoke launches the **actual standalone production Next.js server**, a
real BranchLab API, a temporary loopback HTTPS terminator, and a dedicated
Playwright browser. It exercises authenticated paired execution and downloads,
anonymous access denial, login/logout, cookie properties, rate limiting, origin
checks, constrained PR submission, and desktop/mobile layout. It requires the
repository `.venv`, Chrome (`CHROME_PATH` may override the path), and OpenSSL.
Its random credentials and self-signed certificate are temporary test artifacts;
it does not create production secrets or prove an external GitHub installation.

Use `docker compose --env-file .env.deploy logs --tail 100 api web worker` for service diagnostics, but do
not expose those logs publicly. Back up the evidence volume and App queue with
the services stopped or using a consistent database snapshot. Private repository
source and reports are private workspace data. Keep the Docker host and these
dependencies current.

**Verification scope:** production standalone/HTTPS/browser interaction has been
tested locally. A public DNS name, externally trusted certificate, GitHub App
installation, live webhook delivery, and the full Compose stack must be verified
on the actual deployment host; a passing local smoke is not proof they exist.
