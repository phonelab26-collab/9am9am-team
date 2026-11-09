function read(name: string): string {
  return process.env[name]?.trim() ?? '';
}

const missing: string[] = [];

/** Read a required variable, collecting its name instead of throwing one by one. */
function required(name: string): string {
  const value = read(name);
  if (!value) missing.push(name);
  return value;
}

function httpUrl(name: string, fallback: string): string {
  const value = read(name) || fallback;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('not http(s)');
  } catch {
    throw new Error(`${name} must be a valid http(s) URL (got ${JSON.stringify(value)}).`);
  }
  return value.replace(/\/+$/, '');
}

function positiveNumber(name: string, fallback: number): number {
  const raw = read(name);
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number (got ${JSON.stringify(raw)}).`);
  }
  return value;
}

const port = positiveNumber('PORT', 8787);
if (!Number.isInteger(port) || port > 65535) {
  throw new Error('PORT must be an integer between 1 and 65535.');
}

const xaiKey = required('XAI_API_KEY');
const composioKey = required('COMPOSIO_API_KEY');
const orgoKey = required('ORGO_API_KEY');
const sessionSecret = required('SESSION_SECRET');

if (missing.length) {
  throw new Error(
    `Missing required environment variable${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}.\n` +
      'Copy .env.example to .env, fill in the values, then start again. See README.md for details.',
  );
}
if (sessionSecret.length < 32) {
  throw new Error('SESSION_SECRET must be at least 32 characters. Generate one with: openssl rand -hex 32');
}

const appUrl = httpUrl('APP_URL', 'http://localhost:5173');
const serverUrl = httpUrl('SERVER_URL', `http://localhost:${port}`);
const devLogin = read('DEV_LOGIN') === '1';
const secureCookies = read('COOKIE_SECURE') === '1' || serverUrl.startsWith('https://');
const orgoWorkspaceId = read('ORGO_WORKSPACE_ID');

if (devLogin && !/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/.test(appUrl)) {
  console.warn(`[9am9am] WARNING: DEV_LOGIN=1 is enabled while APP_URL=${appUrl}. Disable it before exposing this server.`);
}
if (!orgoWorkspaceId) {
  console.warn('[9am9am] ORGO_WORKSPACE_ID is empty: computer tasks will fail until it is set.');
}

export const env = {
  port,
  appUrl,
  /** Public URL of this server, used for OAuth callbacks. */
  serverUrl,
  xaiKey,
  xaiModel: read('XAI_MODEL') || 'grok-4.7',
  composioKey,
  orgoKey,
  /** Orgo workspace that holds every user's box. */
  orgoWorkspaceId,
  /** Orgo box size for new users. */
  orgoRamGb: positiveNumber('ORGO_RAM_GB', 8),
  orgoCpu: positiveNumber('ORGO_CPU', 2),
  orgoResolution: read('ORGO_RESOLUTION') || '1280x800x24',
  sessionSecret,
  devLogin,
  /** Secure cookies are automatic on HTTPS; COOKIE_SECURE=1 forces them behind a TLS proxy. */
  secureCookies,
  isProduction: read('NODE_ENV') === 'production',
  dbPath: read('DB_PATH') || new URL('../../data/9am9am.db', import.meta.url).pathname,
};
