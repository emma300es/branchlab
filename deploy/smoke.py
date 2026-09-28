"""Exercise the real Compose/TLS/login/proxy stack with disposable CI credentials.

Never uses deployment credentials or prints generated tokens. TLS verification is
disabled ONLY for this loopback smoke test's explicitly self-signed certificate.
"""
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".branchlab/deploy-smoke"


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    (ROOT / ".branchlab/unconfigured-app.pem").touch(exist_ok=True)
    values = {
        "BRANCHLAB_PUBLIC_ORIGIN": "https://branchlab.test",
        "BRANCHLAB_HOST": "branchlab.test",
        "BRANCHLAB_OWNER_TOKEN": secrets.token_urlsafe(48),
        "BRANCHLAB_SESSION_SECRET": secrets.token_urlsafe(48),
        "BRANCHLAB_API_TOKEN": secrets.token_urlsafe(48),
    }
    target = ROOT / ".env.deploy"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (STATE / "Caddyfile").write_text("""branchlab.test {
  tls internal
  handle /api/github/webhook {
    reverse_proxy api:8765
  }
  handle {
    reverse_proxy web:3000
  }
}
""")
    (STATE / "override.yml").write_text("services:\n  https:\n    volumes:\n"
                                          f"      - {STATE}/Caddyfile:/etc/caddy/Caddyfile:ro\n")
    print("Prepared ephemeral CI HTTPS configuration (credentials not displayed).")


def request(path, method="GET", body=None, cookie=None, origin="https://branchlab.test"):
    context = ssl._create_unverified_context()
    connection = http.client.HTTPSConnection("branchlab.test", timeout=120, context=context)
    headers = {"Host": "branchlab.test"}
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        headers.update({"Content-Type": "application/json", "Origin": origin})
        body = json.dumps(body)
    # Keep transport on loopback but send the named site's TLS SNI. An HTTP Host
    # header alone cannot select Caddy's certificate during the earlier handshake.
    try:
        raw_socket = socket.create_connection(("127.0.0.1", 443), timeout=120)
        try:
            connection.sock = context.wrap_socket(raw_socket, server_hostname="branchlab.test")
        except BaseException:
            raw_socket.close()
            raise
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def verify():
    values = dict(line.split("=", 1) for line in (ROOT / ".env.deploy").read_text().splitlines())
    for _ in range(60):
        try:
            if request("/login")[0] == 200:
                break
        except (OSError, ssl.SSLError, http.client.HTTPException):
            pass
        time.sleep(2)
    else:
        raise RuntimeError("HTTPS dashboard never became ready")
    assert request("/api/runs")[0] == 401
    assert request("/")[0] in (302, 303, 307, 308)
    status, _, _ = request("/api/session", "POST", {"token": "incorrect"})
    assert status == 401, status
    status, headers, _ = request("/api/session", "POST", {"token": values["BRANCHLAB_OWNER_TOKEN"]})
    assert status == 200, status
    set_cookie = next(v for k, v in headers.items() if k.lower() == "set-cookie")
    assert "httponly" in set_cookie.lower() and "secure" in set_cookie.lower()
    cookie = set_cookie.split(";", 1)[0]
    assert request("/api/runs", cookie=cookie)[0] == 200
    assert request("/api/demo", "POST", {}, cookie, origin="https://attacker.example")[0] == 403
    status, _, body = request("/api/demo", "POST", {}, cookie)
    assert status == 200, (status, body[:200])
    report = json.loads(body)
    assert report["summary"]["suspected_regression"] == 1
    assert report["provider"]["live_ai"] is False
    artifact = f"/api/runs/{report['id']}/report.json"
    assert request(artifact)[0] == 401
    assert request(artifact, cookie=cookie)[0] == 200
    assert request("/api/session", "DELETE", {}, cookie)[0] in (200, 204)
    print("HTTPS stack passed: login, HttpOnly/Secure session, protected evidence, CSRF rejection, real demo and logout.")


if __name__ == "__main__":
    {"prepare": prepare, "verify": verify}[sys.argv[1]]()
