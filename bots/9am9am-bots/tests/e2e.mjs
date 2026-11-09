// 9AM9AM Bots end-to-end smoke test (real backend, real Grok 4.7, real Orgo computer).
// Usage: node tests/e2e.mjs [baseUrl]   (default http://localhost:8787)
// Creates a throwaway "QA" bot on the dev account, exercises the core flows, then deletes it.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.argv[2] ?? 'http://localhost:8787';
const OUT = path.resolve(new URL('.', import.meta.url).pathname, 'out');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const step = async (name, fn) => {
  const t0 = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, ms: Date.now() - t0 });
    console.log(`PASS ${name} (${Date.now() - t0}ms)`);
  } catch (e) {
    try { await page.screenshot({ path: path.join(OUT, `FAIL-${name.replace(/[^a-z0-9]+/gi, '-')}.png`) }); } catch {}
    results.push({ name, ok: false, ms: Date.now() - t0, error: String(e?.message ?? e).split('\n')[0] });
    console.log(`FAIL ${name}: ${String(e?.message ?? e).split('\n')[0]}`);
  }
};

const login = await fetch(`${BASE}/api/auth/dev`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.E2E_EMAIL ?? 'dev@example.com', name: 'Dev' }) });
const cookie = (login.headers.get('set-cookie') ?? '').match(/9am9am_session=([^;]+)/)?.[1];
if (!cookie) throw new Error('dev login failed: ' + login.status);
const api = (p, init = {}) => fetch(`${BASE}${p}`, { ...init, headers: { 'content-type': 'application/json', cookie: `9am9am_session=${cookie}`, ...(init.headers ?? {}) } });

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1512, height: 864 } });
const host = new URL(BASE).hostname;
await ctx.addCookies([{ name: '9am9am_session', value: cookie, domain: host, path: '/' }]);
const page = await ctx.newPage();
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
const shot = (n) => page.screenshot({ path: path.join(OUT, `${n}.png`) });
const botName = `QA ${new Date().toISOString().slice(11, 19).replace(/:/g, '')}`;
let botId = null;

await step('app loads with sidebar', async () => {
  await page.goto(BASE);
  await page.getByText('Quinn').first().waitFor({ timeout: 15000 });
  await shot('01-loaded');
});

await step('create bot via modal', async () => {
  await page.getByLabel('New Bot').first().click();
  await page.getByPlaceholder('Give your Bot a name').fill(botName);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.getByLabel(`Message ${botName}`).or(page.getByPlaceholder(new RegExp(`Message ${botName}`))).first().waitFor({ timeout: 15000 });
  const bots = await (await api('/api/bots')).json();
  botId = bots.bots.find((b) => b.name === botName)?.id;
  if (!botId) throw new Error('bot not found via API');
  await shot('02-created');
});

const send = async (text) => {
  const box = page.getByPlaceholder(new RegExp(`Message ${botName}`)).first();
  await box.fill(text);
  await box.press('Enter');
};
const waitReply = async (re, timeout) => {
  await page.waitForFunction(
    ([src]) => [...document.querySelectorAll('body *')].some((el) => el.childElementCount === 0 && new RegExp(src, 'i').test(el.textContent ?? '')),
    [re.source],
    { timeout },
  );
};

await step('chat reply streams (grok-4.7)', async () => {
  await send('What is 17 times 3? Reply with only the number immediately followed by -e2e (no spaces).');
  await waitReply(/\b51-e2e/, 90000);
  await shot('03-chat');
});

await step('bot uses its own computer (shell on its screen)', async () => {
  await send('Use your shell tool to run `echo 9am9am-$((40+2))-e2e` and reply with only the output.');
  await waitReply(/9am9am-42-e2e/, 240000);
  const comp = await (await api('/api/computer')).json();
  if (!comp.computer.screens.some((s) => s.botId === botId)) throw new Error('no screen leased for QA bot');
  await shot('04-computer-tool');
});

await step('computer viewer: watch, take control, hand back', async () => {
  const card = page.getByText("Bot's computer").first();
  if (!(await card.isVisible())) await page.getByLabel('Show details').first().click();
  await page.getByText("Bot's computer").first().click();
  await page.locator('canvas').first().waitFor({ timeout: 30000 });
  await page.waitForTimeout(2500);
  await shot('05-viewer-watch');
  await page.getByRole('button', { name: 'Take control' }).click();
  await page.getByRole('button', { name: "I'm done" }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  await shot('06-viewer-control');
  await page.getByRole('button', { name: "I'm done" }).click();
  await page.getByRole('button', { name: 'Take control' }).waitFor({ timeout: 15000 });
  await page.keyboard.press('Escape');
});

await step('search palette opens Quinn', async () => {
  await page.keyboard.press('Control+k');
  await page.getByPlaceholder(/Search Bots/).fill('Quinn');
  await page.keyboard.press('Enter');
  await page.getByPlaceholder(/Message Quinn/).first().waitFor({ timeout: 10000 });
  await shot('07-search');
});

await step('connect apps modal lists Gmail', async () => {
  await page.getByLabel('Connect apps').first().click();
  await page.getByPlaceholder(/Search apps/).fill('gmail');
  await page.getByText('Gmail', { exact: true }).first().waitFor({ timeout: 20000 });
  await shot('08-apps');
  await page.keyboard.press('Escape');
});

await step('voice: session secret + realtime greeting audio', async () => {
  const sess = await (await api('/api/voice/session', { method: 'POST', body: JSON.stringify({ botId }) })).json();
  if (!sess.clientSecret) throw new Error('no client secret: ' + JSON.stringify(sess).slice(0, 200));
  const { default: WebSocket } = await import('ws');
  const audioChunks = await new Promise((resolve, reject) => {
    const ws = new WebSocket(sess.websocketUrl, [`xai-client-secret.${sess.clientSecret}`]);
    let n = 0;
    const t = setTimeout(() => { ws.close(); n ? resolve(n) : reject(new Error('no audio within 25s')); }, 25000);
    ws.on('open', () => {
      ws.send(JSON.stringify({ type: 'session.update', session: sess.session }));
      ws.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Say hi in five words.' }] } }));
      ws.send(JSON.stringify({ type: 'response.create' }));
    });
    ws.on('message', (d) => {
      const ev = JSON.parse(d.toString());
      if (/audio\.delta$/.test(ev.type)) n++;
      if (ev.type === 'response.done' && n) { clearTimeout(t); ws.close(); resolve(n); }
      if (ev.type === 'error') { clearTimeout(t); ws.close(); reject(new Error(ev.error?.message ?? 'realtime error')); }
    });
  });
  console.log(`   voice audio chunks: ${audioChunks}`);
  await page.getByText('Start voice chat').first().waitFor({ timeout: 10000 });
});

await step('group chat: members reply via @mention', async () => {
  // Throwaway group: the QA bot plus the primary bot; @mention the QA bot.
  const bots = (await (await api('/api/bots')).json()).bots;
  const qa = bots.find((b) => b.id === botId);
  const primary = bots.find((b) => b.isPrimary);
  const created = await (await api('/api/rooms', { method: 'POST', body: JSON.stringify({ name: `E2E group ${Date.now() % 100000}`, botIds: [botId, primary.id] }) })).json();
  if (!created.id) throw new Error('could not create group: ' + JSON.stringify(created));
  try {
    await api(`/api/bots/${created.id}/messages`, { method: 'POST', body: JSON.stringify({ text: `@${qa.name} reply with exactly: group-e2e-ok` }) });
    const t0 = Date.now();
    for (;;) {
      const rows = (await (await api(`/api/bots/${created.id}/rows`)).json()).rows;
      if (rows.some((r) => r.body?.from?.id === botId && /group-e2e-ok/i.test(r.body.text ?? ''))) break;
      if (Date.now() - t0 > 120000) throw new Error('QA bot did not reply in the group');
      await new Promise((r) => setTimeout(r, 2000));
    }
  } finally {
    await api(`/api/bots/${created.id}`, { method: 'DELETE' });
  }
});

await step('bot settings: general, skills, memories, routines', async () => {
  await page.getByText('Quinn').first().click();
  await page.getByLabel('Bot settings').click();
  const nav = page.getByLabel('Bot settings sections');
  await nav.waitFor({ timeout: 10000 });
  for (const t of ['Skills', 'Memories', 'Routines']) { await nav.getByText(t, { exact: true }).click(); await page.waitForTimeout(400); }
  await nav.getByText('Memories', { exact: true }).click();
  // The memory search box only shows with more than 6 memories, so wait for the page title instead.
  await page.locator('.cfg-page__title', { hasText: 'Memories' }).waitFor({ timeout: 10000 });
  await shot('09-bot-settings-memories');
  await page.keyboard.press('Escape');
});

await step('details tabs: library + tasks', async () => {
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Tasks', exact: true }).last().click();
  await page.waitForTimeout(500);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
});

await step('app settings: computer screens + permissions', async () => {
  await page.locator('.account__btn').click();
  await page.getByText('Settings', { exact: true }).first().click();
  await page.locator('.cfg-nav__item', { hasText: 'Computer' }).click();
  await page.getByText(/screens in use/i).first().waitFor({ timeout: 10000 });
  await shot('10-settings-computer');
  await page.locator('.cfg-nav__item', { hasText: 'Permissions' }).click();
  await page.waitForTimeout(500);
  await page.keyboard.press('Escape');
});

if (botId) await api(`/api/bots/${botId}`, { method: 'DELETE' });
await browser.close();

const summary = { base: BASE, at: new Date().toISOString(), passed: results.filter((r) => r.ok).length, failed: results.filter((r) => !r.ok).length, results, consoleErrors: consoleErrors.slice(0, 20) };
fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(`\n${summary.passed} passed, ${summary.failed} failed. Console errors: ${consoleErrors.length}. Screenshots in ${OUT}`);
process.exit(summary.failed ? 1 : 0);
