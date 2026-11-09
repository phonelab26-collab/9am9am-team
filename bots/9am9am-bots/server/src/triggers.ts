import { db, now } from './db.js';
import { composio } from './composio.js';
import { appendRow } from './store.js';

/**
 * Event routines: Composio triggers (e.g. GMAIL_NEW_GMAIL_MESSAGE, SLACK_RECEIVE_MESSAGE,
 * GITHUB_PULL_REQUEST_EVENT) that wake a bot with a prompt. Stored in the `routines` table
 * with schedule `event:<TRIGGER_SLUG>` so they show up alongside cron routines.
 * Delivery uses Composio's realtime subscription (no public webhook URL needed).
 */

let enqueueFn: ((botId: string, t: any) => void) | null = null;
let subscribed = false;

export const isEventSchedule = (schedule: string) => schedule.startsWith('event:');

function migrate() {
  for (const col of ['trigger_id TEXT', 'trigger_config TEXT']) {
    try { db.exec(`ALTER TABLE routines ADD COLUMN ${col}`); } catch { /* already there */ }
  }
}

export async function initTriggers(enqueue: (botId: string, t: any) => void) {
  migrate();
  enqueueFn = enqueue;
  const count = (db.prepare("SELECT COUNT(*) n FROM routines WHERE schedule LIKE 'event:%'").get() as any).n;
  if (count > 0) await ensureSubscribed();
  console.log(`[triggers] ${count} event routine(s)`);
}

async function ensureSubscribed() {
  if (subscribed) return;
  subscribed = true;
  try {
    await composio.triggers.subscribe(
      (data: any) => onTrigger(data),
      undefined,
      (err) => console.warn('[triggers] subscription error', JSON.stringify(err).slice(0, 300)),
    );
    console.log('[triggers] realtime subscription active');
  } catch (e) {
    subscribed = false;
    console.warn('[triggers] subscribe failed', (e as Error).message);
  }
}

function onTrigger(data: any) {
  const ids = [data?.id, data?.metadata?.id, data?.metadata?.nanoId].filter(Boolean);
  if (!ids.length) return;
  const rows = db.prepare(`SELECT * FROM routines WHERE trigger_id IN (${ids.map(() => '?').join(',')}) AND enabled=1`).all(...ids) as any[];
  for (const r of rows) {
    const payload = JSON.stringify(data.payload ?? data.originalPayload ?? {}, null, 1).slice(0, 8000);
    db.prepare('UPDATE routines SET last_run_at=? WHERE id=?').run(now(), r.id);
    appendRow(r.bot_id, 'event', { type: 'event', event: 'routine-run', text: `Routine triggered: ${r.name}`, ref: r.id });
    enqueueFn?.(r.bot_id, {
      type: 'routine',
      routineId: r.id,
      name: r.name,
      prompt: `${r.prompt}\n\n[Event: ${data.triggerSlug ?? r.schedule.slice(6)}]\n${payload}`,
    });
  }
}

export async function listTriggerTypes(toolkit: string) {
  const r: any = await composio.triggers.listTypes({ toolkits: [toolkit] } as any);
  const items: any[] = r?.items ?? r ?? [];
  return items.slice(0, 40).map((t) => ({
    slug: t.slug,
    name: t.name,
    description: String(t.description ?? '').slice(0, 300),
    config: t.config?.properties ? { properties: t.config.properties, required: t.config.required ?? [] } : t.config ?? {},
  }));
}

export async function createEventRoutine(userId: string, botId: string, a: { name: string; prompt: string; trigger_slug: string; config?: Record<string, unknown> }) {
  migrate();
  const slug = a.trigger_slug.toUpperCase();
  const res: any = await composio.triggers.create(userId, slug, { triggerConfig: a.config ?? {} } as any);
  const triggerId = res?.triggerId ?? res?.trigger_id ?? res?.id;
  if (!triggerId) throw new Error(`Composio did not return a trigger id: ${JSON.stringify(res).slice(0, 200)}`);
  const id = `rt_${Math.random().toString(36).slice(2, 12)}`;
  db.prepare('INSERT INTO routines(id,bot_id,name,prompt,schedule,timezone,enabled,created_at,trigger_id,trigger_config) VALUES (?,?,?,?,?,?,1,?,?,?)')
    .run(id, botId, a.name, a.prompt, `event:${slug}`, 'UTC', now(), triggerId, JSON.stringify(a.config ?? {}));
  await ensureSubscribed();
  return { id, triggerId };
}

/** Called by the routine scheduler when an event routine is enabled/disabled/deleted. */
export async function setEventRoutineEnabled(routineId: string, enabled: boolean) {
  const r = db.prepare('SELECT trigger_id FROM routines WHERE id=?').get(routineId) as any;
  if (!r?.trigger_id) return;
  try {
    if (enabled) { await composio.triggers.enable(r.trigger_id); await ensureSubscribed(); }
    else await composio.triggers.disable(r.trigger_id);
  } catch (e) {
    console.warn('[triggers] toggle failed', routineId, (e as Error).message);
  }
}

export function describeEvent(schedule: string) {
  const slug = schedule.slice(6);
  const words = slug.split('_');
  const app = words.shift() ?? '';
  return `When ${app.charAt(0) + app.slice(1).toLowerCase()}: ${words.join(' ').toLowerCase()}`;
}

/** Remove the Composio trigger behind an event routine (call before deleting the routine row). */
export async function deleteEventTrigger(routineId: string) {
  const r = db.prepare('SELECT trigger_id FROM routines WHERE id=?').get(routineId) as any;
  if (!r?.trigger_id) return;
  try { await composio.triggers.delete(r.trigger_id); } catch (e) { console.warn('[triggers] delete failed', routineId, (e as Error).message); }
}
