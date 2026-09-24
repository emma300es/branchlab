# Probe container

Build from the repository root:

```sh
docker build -f docker/runner.Dockerfile -t branchlab-runner:0.1 .
```

The runtime does not build or pull an image automatically. Each probe uses a fresh
container with a read-only snapshot, no network, non-root uid, no capabilities,
no-new-privileges, bounded memory/CPU/PIDs/output, and a disposable output mount.
Repository dependencies beyond the supplied FastAPI/httpx stack require an
explicitly reviewed custom image; dependency installation does not run against
untrusted repositories. An unavailable image/daemon is an execution error, not a
regression. Containers share the host kernel and are not a perfect isolation
boundary; use an isolated VM/runner host for actively hostile repositories.

The `--runner subprocess --trust-local-code` option is only for code you already trust. Its
fresh process, environment allowlist, timeout and rlimits mitigate accidents but
do not prevent filesystem/network access or malicious code from forging results.
It is never selected as an automatic Docker fallback.
