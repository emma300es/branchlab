"use client";
import { FormEvent, useState } from "react";
import { Icon } from "./icons";

export default function Login() {
  const [token, setToken] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
      setToken("");
      if (!response.ok) { const data = await response.json(); throw new Error(data.error ?? "Sign-in failed."); }
      window.location.replace("/");
    } catch (e) { setError(e instanceof Error ? e.message : "Sign-in failed."); }
    finally { setBusy(false); }
  }
  return <main className="login-shell"><section className="login-card"><a className="brand" href="/"><span className="brand-icon"><Icon name="branch"/></span><span>branch<span className="brand-light">lab</span></span></a><div className="eyebrow teal">YOUR PRIVATE WORKSPACE</div><h1>Good evidence.<br/>Just for you.</h1><p>Sign in to investigate changes and explore your reports.</p><form onSubmit={submit}><label htmlFor="access-token">Owner access token</label><input id="access-token" name="token" type="password" value={token} autoComplete="current-password" required maxLength={4096} onChange={event => setToken(event.target.value)}/>{error && <p role="alert" className="login-error">{error}</p>}<button type="submit" className="button primary" disabled={busy}>{busy ? "Signing in…" : "Open workspace"}<Icon name="arrow" width="16"/></button></form><small>Use the access token configured by your deployment administrator.</small></section></main>;
}
