// Real HTTPS browser + standalone Next server + actual BranchLab API.
// Ephemeral test-only credentials/certificate remain in memory/temp, never printed.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import http from 'node:http';
import https from 'node:https';

const root = path.resolve('..');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'branchlab-auth-smoke-'));
const owner = randomBytes(32).toString('hex');
const session = randomBytes(32).toString('hex');
const backend = randomBytes(32).toString('hex');
const origin = 'https://localhost:13443';
const children = []; let browser; let proxy;
let logs = '';
function start(command, args, env) {
  const child = spawn(command, args, { cwd: root, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
  child.stdout.on('data', data => { logs += data; }); child.stderr.on('data', data => { logs += data; });
  children.push(child); return child;
}
async function wait(url) {
  for (let i = 0; i < 60; i++) { try { const response = await fetch(url); if (response.status < 500) return; } catch {} await new Promise(resolve => setTimeout(resolve, 500)); }
  throw new Error(`Service did not start: ${url}`);
}
try {
  await fs.cp('.next/static', '.next/standalone/.next/static', { recursive: true });
  await fs.cp('public', '.next/standalone/public', { recursive: true });
  start(path.join(root, '.venv/bin/branchlab'), ['serve', '--port', '18765', '--data', path.join(temp, 'runs')], { BRANCHLAB_PUBLIC_ORIGIN: '', BRANCHLAB_API_TOKEN: backend, BRANCHLAB_GITHUB_APP_ID: '' });
  start(process.execPath, [path.join(root, 'web/.next/standalone/server.js')], { HOSTNAME: '127.0.0.1', PORT: '13007', BRANCHLAB_PUBLIC_ORIGIN: origin, BRANCHLAB_OWNER_TOKEN: owner, BRANCHLAB_SESSION_SECRET: session, BRANCHLAB_API_TOKEN: backend, BRANCHLAB_API_URL: 'http://127.0.0.1:18765' });
  await wait('http://127.0.0.1:18765/api/health');
  await wait('http://127.0.0.1:13007/login'); // Host mismatch is 403; still proves listener ready.
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', path.join(temp, 'key.pem'), '-out', path.join(temp, 'cert.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], { stdio: 'ignore' });
  proxy = https.createServer({ key: await fs.readFile(path.join(temp, 'key.pem')), cert: await fs.readFile(path.join(temp, 'cert.pem')) }, (request, response) => {
    const upstream = http.request({ hostname: '127.0.0.1', port: 13007, path: request.url, method: request.method, headers: request.headers }, upstreamResponse => { response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers); upstreamResponse.pipe(response); });
    upstream.on('error', () => { response.writeHead(502); response.end(); }); request.pipe(upstream);
  });
  await new Promise(resolve => proxy.listen(13443, '127.0.0.1', resolve));
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox', '--host-resolver-rules=MAP localhost 127.0.0.1'] });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage(); await page.setViewportSize({ width: 1600, height: 1100 });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const anonymous = await page.request.get(`${origin}/api/runs`); assert.equal(anonymous.status(), 401);
  const bypass = await page.request.get(`${origin}/api/runs`, { headers: { 'x-middleware-subrequest': 'proxy:proxy:proxy:proxy:proxy' } }); assert.equal(bypass.status(), 401);
  const pageResponse = await page.goto(origin); assert.equal(page.url(), `${origin}/login`); assert.equal(pageResponse.status(), 200);
  const loginMarkup = await page.content(); for (const secret of [owner, session, backend]) assert.ok(!loginMarkup.includes(secret));
  const crossLogin = await page.request.post(`${origin}/api/session`, { headers: { Origin: 'https://evil.example' }, data: { token: owner } }); assert.equal(crossLogin.status(), 403);
  await page.getByLabel('Owner access token').fill(owner);
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await page.getByText('API connected', { exact: true }).waitFor({ timeout: 20_000 });
  const cookies = await context.cookies(); const cookie = cookies.find(value => value.name === '__Host-branchlab-session');
  assert.ok(cookie?.httpOnly && cookie.secure && cookie.sameSite === 'Strict'); assert.ok(!cookie.value.includes(owner));
  const direct = await fetch('http://127.0.0.1:18765/api/runs'); assert.equal(direct.status, 401, 'backend read requires bearer');
  const clientBearer = await page.request.get(`${origin}/api/runs`, { headers: { Authorization: 'Bearer client-must-not-forward' } }); assert.equal(clientBearer.status(), 200);
  const demoPromise = page.waitForResponse(response => response.url().endsWith('/api/demo') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Run local demo', exact: true }).click();
  const demoResponse = await demoPromise; assert.equal(demoResponse.status(), 200);
  const report = await demoResponse.json(); await page.locator('.run-id').getByText(report.id, { exact: true }).waitFor();
  assert.ok(report.summary.suspected_regression > 0); await page.getByText('Not a live AI result', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Source citations', exact: false }).click(); await page.getByText('Source citations validated', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Impact map', exact: true }).click(); assert.ok(await page.getByRole('img', { name: /Impact graph/ }).count());
  await page.getByRole('button', { name: 'Probe plan', exact: true }).click(); await page.getByRole('heading', { name: 'Declarative HTTP probe' }).waitFor();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Export report' }).click()]); assert.match(download.suggestedFilename(), /^run-.*\.md$/);
  await page.getByLabel('GitHub pull-request URL').fill('https://github.com/example/not-installed/pull/1');
  await page.getByRole('button', { name: 'Investigate PR', exact: true }).click(); await page.getByRole('alert').filter({ hasText: 'GitHub App installation' }).waitFor();
  await page.screenshot({ path: '.smoke-private-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: '.smoke-private-mobile.png', fullPage: true });
  const crossWrite = await page.request.post(`${origin}/api/demo`, { headers: { Origin: 'https://evil.example' }, data: {} }); assert.equal(crossWrite.status(), 403);
  const invalidPR = await page.request.post(`${origin}/api/investigations`, { headers: { Origin: origin }, data: { pr_url: 'file:///etc/passwd', runner: 'subprocess' } }); assert.equal(invalidPR.status(), 400);
  const oversized = await page.request.post(`${origin}/api/investigations`, { headers: { Origin: origin }, data: { pr_url: 'x'.repeat(4096) } }); assert.equal(oversized.status(), 413);
  const webhook = await page.request.post(`${origin}/api/github/webhook`, { headers: { Origin: origin }, data: {} }); assert.equal(webhook.status(), 404);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click(); await page.waitForURL(`${origin}/login`);
  const afterLogout = await page.request.get(`${origin}/api/runs/${report.id}/report.json`); assert.equal(afterLogout.status(), 401);
  const forged = await page.request.get(`${origin}/api/runs`, { headers: { Cookie: '__Host-branchlab-session=forged.invalid' } }); assert.equal(forged.status(), 401);
  for (let i = 0; i < 5; i++) {
    const response = await page.request.post(`${origin}/api/session`, { headers: { Origin: origin }, data: { token: 'wrong' } });
    assert.equal(response.status(), i === 4 ? 429 : 401);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, mode: 'actual standalone Next + HTTPS proxy + real API + fixture execution', checks: ['anonymous page/API protection', 'HTTPS HttpOnly session', 'login/logout', 'forged cookie rejected', 'global login rate limit', 'same-origin writes', 'server-only bearer forwarding', 'actual paired-execution demo', 'citations/impact/probe views', 'report download', 'bounded PR form and unavailable-App state', 'route/body allowlist', 'desktop/mobile layout', 'no browser errors'], externalGitHubApp: 'not installed by this test', screenshots: ['web/.smoke-private-desktop.png', 'web/.smoke-private-mobile.png'] }, null, 2));
} catch (error) {
  // Startup output is bounded and test secrets are redacted before diagnostics.
  let safe = logs.slice(-4000); for (const secret of [owner, session, backend]) safe = safe.split(secret).join('[redacted]');
  console.error(safe); throw error;
} finally {
  await browser?.close(); if (proxy) { proxy.closeAllConnections(); await new Promise(resolve => proxy.close(resolve)); }
  for (const child of children) { try { process.kill(-child.pid, 'SIGTERM'); } catch {} }
  await fs.rm(temp, { recursive: true, force: true });
}
