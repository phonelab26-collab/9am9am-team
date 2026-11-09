import { env } from './env.js';

const BASE = 'https://www.orgo.ai/api';

export class OrgoError extends Error {
  constructor(public status: number, public code: string | undefined, message: string, public body?: unknown) {
    super(message);
  }
}

async function call<T = any>(method: string, path: string, body?: unknown, opts: { timeoutMs?: number; headers?: Record<string, string> } = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 120_000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${env.orgoKey}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...opts.headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    let json: any = undefined;
    try { json = text ? JSON.parse(text) : undefined; } catch { json = { raw: text }; }
    if (!res.ok) {
      const err = json?.error;
      const msg = typeof err === 'string' ? err : err?.message ?? `${res.status} ${res.statusText}`;
      throw new OrgoError(res.status, json?.code ?? err?.code, `Orgo ${method} ${path}: ${msg}`, json);
    }
    return json as T;
  } finally {
    clearTimeout(timer);
  }
}

export interface OrgoComputer {
  id: string;
  instance_id?: string;
  status: string;
  name: string;
  resolution?: string;
  vnc_password?: string | null;
  asleep?: { where: 'host' | 'storage'; waking?: { expected_seconds?: number } } | null;
}

export interface OrgoScreen { id: string; display: string; width: number; height: number; default: boolean }

const q = (screen?: string) => (screen && screen !== 'default' ? `?screen=${encodeURIComponent(screen)}` : '');

export const orgo = {
  workspaces: () => call<any>('GET', '/workspaces'),

  createComputer: (name: string, idempotencyKey: string) =>
    call<OrgoComputer>(
      'POST',
      '/computers',
      { workspace_id: env.orgoWorkspaceId, name, os: 'linux', ram: env.orgoRamGb, cpu: env.orgoCpu, resolution: env.orgoResolution },
      { timeoutMs: 300_000, headers: { 'Idempotency-Key': idempotencyKey } },
    ),
  getComputer: (id: string) => call<OrgoComputer>('GET', `/computers/${id}`),
  start: (id: string) => call<any>('POST', `/computers/${id}/start`, {}, { timeoutMs: 320_000 }),
  resume: (id: string) => call<any>('POST', `/computers/${id}/resume`, {}, { timeoutMs: 200_000 }),
  suspend: (id: string) => call<any>('POST', `/computers/${id}/suspend`, {}),
  vncPassword: (id: string) => call<{ password: string; desktop_api_token: string }>('GET', `/computers/${id}/vnc-password`),

  listScreens: (id: string) => call<{ screens: OrgoScreen[] } | OrgoScreen[]>('GET', `/computers/${id}/screens`).then((r) => (Array.isArray(r) ? r : r.screens ?? [])),
  createScreen: (id: string) => call<OrgoScreen>('POST', `/computers/${id}/screens`, {}),
  getScreen: (id: string, screen: string) => call<OrgoScreen>('GET', `/computers/${id}/screens/${screen}`),
  deleteScreen: (id: string, screen: string) => call<void>('DELETE', `/computers/${id}/screens/${screen}`),

  screenshot: (id: string, screen?: string) =>
    call<{ image: string; mime_type?: string; width: number; height: number }>(
      'GET',
      `/computers/${id}/screenshot${q(screen)}${screen && screen !== 'default' ? '&' : '?'}response_format=base64&format=jpeg&quality=75`,
    ),
  click: (id: string, screen: string | undefined, b: { x: number; y: number; button?: string; double?: boolean }) => call('POST', `/computers/${id}/click${q(screen)}`, b),
  move: (id: string, screen: string | undefined, b: { x: number; y: number }) => call('POST', `/computers/${id}/mouse-move${q(screen)}`, b),
  drag: (id: string, screen: string | undefined, b: { start_x: number; start_y: number; end_x: number; end_y: number }) => call('POST', `/computers/${id}/drag${q(screen)}`, b),
  type: (id: string, screen: string | undefined, text: string) => call('POST', `/computers/${id}/type${q(screen)}`, { text }),
  key: (id: string, screen: string | undefined, key: string) => call('POST', `/computers/${id}/key${q(screen)}`, { key }),
  scroll: (id: string, screen: string | undefined, b: { x: number; y: number; direction: 'up' | 'down'; amount: number }) => call('POST', `/computers/${id}/scroll${q(screen)}`, b),
  bash: (id: string, screen: string | undefined, command: string, timeout = 120) =>
    call<{ success: boolean; output: string; exit_code: number }>('POST', `/computers/${id}/bash${q(screen)}`, { command, timeout }, { timeoutMs: (timeout + 40) * 1000 }),
  /** Copy a file off a running computer into workspace storage; returns a signed URL (1 h). */
  exportFile: (id: string, path: string) =>
    call<{ url?: string; file?: { id: string; url?: string; filename?: string }; [k: string]: unknown }>('POST', '/files/export', { desktopId: id, path }, { timeoutMs: 180_000 }),
};
