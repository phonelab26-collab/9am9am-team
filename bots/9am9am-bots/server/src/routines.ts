import { Cron } from 'croner';
import { db, now } from './db.js';
import { appendRow } from './store.js';
import type { Routine } from './types.js';
import { isEventSchedule, setEventRoutineEnabled } from './triggers.js';

const jobs = new Map<string, Cron>();
let enqueueFn: ((botId: string, t: any) => void) | null = null;

export function initRoutines(enqueue: (botId: string, t: any) => void) {
  enqueueFn = enqueue;
  const all = db.prepare("SELECT * FROM routines WHERE enabled=1 AND schedule NOT LIKE 'event:%'").all() as any[];
  for (const r of all) scheduleRoutine({ id: r.id, botId: r.bot_id, name: r.name, prompt: r.prompt, schedule: r.schedule, timezone: r.timezone } as Routine);
  console.log(`[routines] scheduled ${jobs.size}`);
}

export function scheduleRoutine(r: Pick<Routine, 'id' | 'botId' | 'name' | 'prompt' | 'schedule' | 'timezone'>) {
  if (isEventSchedule(r.schedule)) { void setEventRoutineEnabled(r.id, true); return; }
  unscheduleRoutine(r.id);
  try {
    const job = new Cron(r.schedule, { timezone: r.timezone || 'UTC', protect: true }, () => runRoutine(r.id));
    jobs.set(r.id, job);
  } catch (e) {
    console.warn('[routines] bad schedule', r.id, r.schedule, (e as Error).message);
  }
}

export function unscheduleRoutine(id: string) {
  const row = db.prepare('SELECT schedule FROM routines WHERE id=?').get(id) as any;
  if (row && isEventSchedule(row.schedule)) void setEventRoutineEnabled(id, false);
  jobs.get(id)?.stop();
  jobs.delete(id);
}

export function runRoutine(id: string) {
  const r = db.prepare('SELECT * FROM routines WHERE id=?').get(id) as any;
  if (!r || !enqueueFn) return;
  db.prepare('UPDATE routines SET last_run_at=? WHERE id=?').run(now(), id);
  appendRow(r.bot_id, 'event', { type: 'event', event: 'routine-run', text: `Running routine: ${r.name}`, ref: id });
  enqueueFn(r.bot_id, { type: 'routine', routineId: id, name: r.name, prompt: r.prompt });
}
