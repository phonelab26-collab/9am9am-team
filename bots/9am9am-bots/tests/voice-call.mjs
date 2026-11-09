// Two-way voice call test: headless Chrome with a fake mic playing tests/ask-weather.wav.
// Usage: node tests/voice-call.mjs <outDir> <throwawayBotId>   (changes that bot's voice settings)
import { createRequire } from 'node:module';
const require = createRequire('/home/t3code/.t3/projects/9am9am-bots/package.json');
const { chromium } = require('playwright');
const S = process.argv[2]; const BOT = process.argv[3]; const BASE = 'http://localhost:8787';
const t0 = Date.now(); const log = (...a) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s]`, ...a);
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: [
  '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${S}/ask-fixed.wav`, '--autoplay-policy=no-user-gesture-required'] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'] });
await ctx.addInitScript((bot) => {
  localStorage.setItem('9am9am.selectedBot', bot); localStorage.setItem('9am9am.detailsOpen', '1');
  window.__audio = { starts: 0, seconds: 0 };
  const orig = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...a) { window.__audio.starts++; window.__audio.seconds += this.buffer?.duration ?? 0; return orig.apply(this, a); };
  const OrigWS = window.WebSocket; window.__ws = { sent: {}, recv: {} };
  window.WebSocket = class extends OrigWS { constructor(u, p) { super(u, p); if (String(u).includes('x.ai')) { this.addEventListener('message', (m) => { try { const t = JSON.parse(m.data).type; window.__ws.recv[t] = (window.__ws.recv[t] ?? 0) + 1; if (t === 'error') (window.__ws.errors ??= []).push(m.data.slice(0, 300)); } catch {} }); const s = this.send.bind(this); this.send = (d) => { try { const t = JSON.parse(d).type; window.__ws.sent[t] = (window.__ws.sent[t] ?? 0) + 1; } catch {} return s(d); }; } } };
}, BOT);
const page = await ctx.newPage();
page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) log('console.' + m.type(), m.text().slice(0, 300)); });
page.on('pageerror', (e) => log('pageerror', e.message));
await page.goto(BASE);
await page.evaluate((email) => fetch('/api/auth/dev', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, name: 'Dev' }) }), process.env.E2E_EMAIL ?? 'dev@example.com');
await page.goto(BASE);
await page.waitForSelector('.composer__input', { timeout: 15000 });
await page.waitForSelector('text=Voice chat');
await page.screenshot({ path: `${S}/shots/01-details.png` });

// --- Preview (TTS → same PCM player path) ---
await page.click('button.voice-preview');
await page.waitForSelector('button.voice-preview:has-text("Playing")', { timeout: 5000 }).catch(() => log('no Playing state seen'));
await page.waitForSelector('button.voice-preview:has-text("Preview")', { timeout: 30000 });
log('preview audio:', JSON.stringify(await page.evaluate(() => window.__audio)), 'error:', await page.locator('.voice-error').textContent().catch(() => null));

// --- settings ---
await page.selectOption('select[aria-label="Voice"]', 'eve');
await page.selectOption('select[aria-label="Speed"]', '1.15');
await page.waitForTimeout(800);
const saved = await page.evaluate(async (bot) => (await (await fetch('/api/bots')).json()).bots.find((b) => b.id === bot), BOT);
log('saved settings:', saved.voice, saved.voiceSpeed, saved.voiceLanguage);

// --- call ---
const before = await page.evaluate(() => window.__audio.starts);
await page.click('button[aria-label^="Start voice chat"]');
await page.waitForSelector('.vcall', { timeout: 5000 });
await page.screenshot({ path: `${S}/shots/02-connecting.png` });
await page.waitForSelector('.vcall.is-live', { timeout: 20000 });
log('live'); log('cta live:', await page.locator('.composer__cta').getAttribute('class'), await page.locator('.composer__cta').evaluate((e) => getComputedStyle(e).backgroundColor));
let lastStatus = ''; let sawWorking = false; let shotWorking = false; let landedAt = 0;
for (let i = 0; i < 200; i++) {
  await page.waitForTimeout(500);
  const st = await page.locator('.vcall__status').textContent().catch(() => '');
  const cap = await page.locator('.vcall__captions').innerText().catch(() => '');
  if (st !== lastStatus) { log('status:', st, '| captions:', cap.replace(/\n/g, ' / ').slice(0, 200)); lastStatus = st; }
  if (st.startsWith('Working') && !shotWorking) { log('cta class:', await page.locator('.composer__cta').getAttribute('class'), await page.locator('.composer__cta').evaluate((e) => getComputedStyle(e).backgroundColor)); sawWorking = true; shotWorking = true; await page.screenshot({ path: `${S}/shots/03-working.png` }); }
  if (sawWorking && !st.includes('Working') && !landedAt) landedAt = Date.now();
  if (landedAt && Date.now() - landedAt > 12000) break;
  if (i === 30 && !sawWorking) await page.screenshot({ path: `${S}/shots/03b-midcall.png` });
}
await page.screenshot({ path: `${S}/shots/04-after-landed.png` });
await page.click('.vcall__captions').catch(() => {});
await page.waitForTimeout(300);
await page.screenshot({ path: `${S}/shots/05-transcript.png` });
log('full call transcript:\n' + (await page.locator('.vcall__log').innerText().catch(() => '(none)')));
log('ws counts:', JSON.stringify(await page.evaluate(() => window.__ws)));
log('audio sources during call:', (await page.evaluate(() => window.__audio.starts)) - before);
await page.click('button[aria-label="End call"]');
await page.waitForTimeout(4000);
await page.screenshot({ path: `${S}/shots/06-ended.png` });
log('events in transcript:', JSON.stringify(await page.locator('.system-event').allInnerTexts()).slice(-500));
await browser.close();
