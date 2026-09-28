import assert from "node:assert/strict";
import { test } from "node:test";
import { authConfig, createSession, LoginLimiter, requestOrigin, sameOriginJson, SESSION_SECONDS, tokenMatches, validSession } from "./auth";

const env = { BRANCHLAB_PUBLIC_ORIGIN: "https://evidence.example", BRANCHLAB_OWNER_TOKEN: "owner-test-only-".repeat(4), BRANCHLAB_SESSION_SECRET: "session-test-only-".repeat(4), BRANCHLAB_API_TOKEN: "backend-test-only-".repeat(4) };
const config = authConfig(env);
test("remote auth requires an exact HTTPS origin and distinct strong configured secrets", () => {
  assert.equal(config.remote, true); assert.equal(config.secure, true); assert.equal(config.cookieName, "__Host-branchlab-session");
  for (const BRANCHLAB_PUBLIC_ORIGIN of ["http://evidence.example", "https://evidence.example/", "https://evidence.example/path", "https://user:pass@evidence.example", "https://evidence.example?token=x"]) assert.throws(() => authConfig({ ...env, BRANCHLAB_PUBLIC_ORIGIN }));
  for (const name of ["BRANCHLAB_OWNER_TOKEN", "BRANCHLAB_SESSION_SECRET", "BRANCHLAB_API_TOKEN"]) assert.throws(() => authConfig({ ...env, [name]: "" }));
  assert.throws(() => authConfig({ ...env, BRANCHLAB_SESSION_SECRET: env.BRANCHLAB_OWNER_TOKEN }));
  assert.throws(() => authConfig({ BRANCHLAB_OWNER_TOKEN: env.BRANCHLAB_OWNER_TOKEN }));
  assert.equal(authConfig({ ...env, BRANCHLAB_PUBLIC_ORIGIN: "http://127.0.0.1:3000" }).secure, false);
});
test("local mode is strictly loopback; forwarded host does not authorize access", () => {
  const local = authConfig({});
  assert.equal(requestOrigin("http://localhost:3000", "127.0.0.1:3000", local), "http://127.0.0.1:3000");
  for (const host of [null, "evil.example", "localhost.evil.example", "127.0.0.1@evil.example", "localhost:3000,evil.example"]) assert.equal(requestOrigin("http://localhost:3000", host, local), null);
  assert.equal(requestOrigin("http://internal:3000", "evidence.example", config), "https://evidence.example");
  assert.equal(requestOrigin("https://evidence.example", "internal:3000", config), null);
});
test("remote writes require exact Origin, JSON, and same-origin fetch metadata", () => {
  const request = (headers = {}) => new Request("http://internal:3000/api/demo", { headers: { host: "evidence.example", origin: "https://evidence.example", "content-type": "application/json", ...headers } });
  assert.equal(sameOriginJson(request(), config), true);
  for (const headers of [{ origin: "https://evil.example" }, { origin: "null" }, { "content-type": "text/plain" }, { "sec-fetch-site": "cross-site" }, { host: "evil.example", "x-forwarded-host": "evidence.example" }]) assert.equal(sameOriginJson(request(headers), config), false);
});
test("sessions expire, are origin-bound, signed, and invalidated by key rotation", () => {
  const now = 1790000000000; const session = createSession(config, now);
  assert.equal(validSession(session, config, now), true);
  assert.equal(validSession(session, config, now + SESSION_SECONDS * 1000), false);
  assert.equal(validSession(session, { ...config, origin: "https://other.example" }, now), false);
  assert.equal(validSession(session, { ...config, sessionSecret: "rotated".repeat(10) }, now), false);
  for (const value of [undefined, "", session + ".extra", session.slice(0, -1), "x".repeat(2050), "e30." + "a".repeat(43)]) assert.equal(validSession(value, config, now), false);
  assert.equal(validSession(session, authConfig({}), now), false);
});
test("token comparison rejects prefixes and wrong lengths; login throttling cannot vary by spoofed IP", () => {
  assert.equal(tokenMatches("secret", "secret"), true); assert.equal(tokenMatches("secre", "secret"), false); assert.equal(tokenMatches("secret2", "secret"), false);
  const limit = new LoginLimiter();
  for (let i = 0; i < 5; i++) assert.equal(limit.allow(100_000), true);
  assert.equal(limit.allow(100_001), false); assert.equal(limit.allow(159_999), false); assert.equal(limit.allow(160_000), true);
});
