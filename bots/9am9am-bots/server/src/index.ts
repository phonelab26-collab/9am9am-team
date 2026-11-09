import { serve } from '@hono/node-server';
import { serveStatic } from '@hono/node-server/serve-static';
import { Hono } from 'hono';
import fs from 'node:fs';
import path from 'node:path';
import type { Server } from 'node:http';
import { db } from './db.js';
import { env } from './env.js';
import { api } from './routes.js';
import { attachVncRelay } from './vnc.js';
import { initRoutines } from './routines.js';
import { initTriggers } from './triggers.js';
import { initProactive } from './proactive.js';
import { initSubagents } from './agent/subagents.js';
import { initRooms } from './agent/rooms.js';
import { checkinsApi } from './routes-proactive.js';
import { roomsApi } from './routes-rooms.js';
import { enqueue } from './agent/loop.js';
import { desSelfTest } from './lib/des.js';
import { configApi } from './routes-config.js';
import { startIdleWatcher } from './idle.js';
import { startMemoryConsolidation } from './agent/memory.js';

if (!desSelfTest()) throw new Error('DES self-test failed');
initSubagents();
initRooms();

const startedAt = Date.now();
const app = new Hono();

// Baseline security headers. No CSP here: the VNC viewer and the OAuth popup
// need a permissive enough policy, and a wrong CSP silently breaks both.
app.use('*', async (c, next) => {
  c.header('X-Content-Type-Options', 'nosniff');
  c.header('X-Frame-Options', 'DENY');
  c.header('Referrer-Policy', 'strict-origin-when-cross-origin');
  c.header('Permissions-Policy', 'camera=(), microphone=(self), geolocation=()');
  if (env.secureCookies) c.header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  await next();
});

app.get('/api/health', (c) => c.json({ ok: true, model: env.xaiModel, uptimeSec: Math.floor((Date.now() - startedAt) / 1000) }));
app.route('/api/checkins', checkinsApi);
app.route('/api/rooms', roomsApi);
app.route('/api', api);
app.route('/api', configApi);

// Serve the built web app in production.
const webDist = path.resolve(new URL('.', import.meta.url).pathname, '../../web/dist');
if (fs.existsSync(webDist)) {
  app.use('/*', serveStatic({ root: path.relative(process.cwd(), webDist) }));
  app.get('*', (c) => c.html(fs.readFileSync(path.join(webDist, 'index.html'), 'utf8')));
}

const server = serve({ fetch: app.fetch, port: env.port }, (info) => console.log(`[9am9am] server on http://localhost:${info.port} (model ${env.xaiModel})`));
attachVncRelay(server as unknown as Server);
// BOTS_NO_SCHEDULER=1 for extra dev/test servers sharing a database copy, so routines and triggers don't fire twice.
if (process.env.BOTS_NO_SCHEDULER !== '1') {
  initRoutines(enqueue);
  void initTriggers(enqueue);
  initProactive(enqueue);
} else console.log('[9am9am] scheduler disabled (BOTS_NO_SCHEDULER=1)');
startIdleWatcher();
startMemoryConsolidation();

let stopping = false;
function shutdown(signal: string) {
  if (stopping) return;
  stopping = true;
  console.log(`[9am9am] ${signal}: shutting down`);
  const force = setTimeout(() => {
    console.error('[9am9am] forced shutdown after 10s');
    process.exit(1);
  }, 10_000);
  force.unref();
  server.close(() => {
    clearTimeout(force);
    try { db.close(); } catch { /* already closed */ }
    console.log('[9am9am] stopped');
    process.exit(0);
  });
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
