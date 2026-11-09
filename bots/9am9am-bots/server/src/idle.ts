import { db, now as dbNow } from './db.js';
import { getLive } from './store.js';
import { suspendComputer } from './computer.js';
import { isBotBusy } from './agent/loop.js';

/**
 * Computer hibernation: suspend a user's Orgo box when no bot turn is running and no
 * viewer WebSocket is connected for BOTS_IDLE_SUSPEND_MIN minutes (default 20).
 * The next computer tool call wakes it again through ensureComputer().
 */

const num = (v: string | undefined, d: number) => (v !== undefined && v !== '' && Number.isFinite(Number(v)) ? Number(v) : d);

export const idleConfig = {
  idleMin: num(process.env.BOTS_IDLE_SUSPEND_MIN, 20),
  sweepSec: num(process.env.BOTS_IDLE_SWEEP_SEC, 60),
  /** Log decisions but never call suspend (for test servers sharing a real VM). */
  dryRun: process.env.BOTS_IDLE_DRY_RUN === '1',
};

// ---------- viewer tracking (called from vnc.ts) ----------

const viewers = new Map<string, number>();
const lastBusy = new Map<string, number>();

export function viewerOpened(userId: string) {
  viewers.set(userId, (viewers.get(userId) ?? 0) + 1);
  lastBusy.set(userId, Date.now());
  console.log(`[idle] viewer connected user=${userId} (now ${viewers.get(userId)})`);
}

export function viewerClosed(userId: string) {
  const n = Math.max(0, (viewers.get(userId) ?? 1) - 1);
  if (n) viewers.set(userId, n); else viewers.delete(userId);
  lastBusy.set(userId, Date.now());
  console.log(`[idle] viewer disconnected user=${userId} (now ${n})`);
}

export const viewerCount = (userId: string) => viewers.get(userId) ?? 0;

// ---------- decision logic (pure, unit-testable) ----------

export interface IdleDeps {
  now(): number;
  /** Computers we might suspend. */
  computers(): { userId: string; status: string }[];
  /** Is any bot of this user mid-turn (queued or running)? */
  anyTurnRunning(userId: string): boolean;
  viewers(userId: string): number;
  /** Latest activity timestamp from storage (bot rows, computer status changes). */
  lastActivity(userId: string): number;
  suspend(userId: string): Promise<void>;
  log(msg: string): void;
}

export type IdleDecision = { action: 'busy'; reason: string } | { action: 'wait'; idleMs: number } | { action: 'suspend'; idleMs: number };

export function createIdleWatcher(deps: IdleDeps, opts: { idleMs: number; dryRun?: boolean }) {
  const startedAt = deps.now();
  const busyAt = new Map<string, number>();
  const announced = new Set<string>();
  const inFlight = new Set<string>();

  function decide(userId: string): IdleDecision {
    const turns = deps.anyTurnRunning(userId);
    const v = deps.viewers(userId);
    if (turns || v > 0) {
      busyAt.set(userId, deps.now());
      announced.delete(userId);
      return { action: 'busy', reason: turns ? 'turn running' : `${v} viewer(s)` };
    }
    const last = Math.max(busyAt.get(userId) ?? startedAt, lastBusy.get(userId) ?? 0, deps.lastActivity(userId));
    const idleMs = deps.now() - last;
    return idleMs >= opts.idleMs ? { action: 'suspend', idleMs } : { action: 'wait', idleMs };
  }

  async function sweep(): Promise<Record<string, IdleDecision>> {
    const out: Record<string, IdleDecision> = {};
    for (const c of deps.computers()) {
      if (c.status !== 'running' || inFlight.has(c.userId)) continue;
      const d = decide(c.userId);
      out[c.userId] = d;
      if (d.action !== 'suspend') continue;
      const mins = Math.round(d.idleMs / 6000) / 10;
      if (opts.dryRun) {
        if (!announced.has(c.userId)) deps.log(`[idle] (dry run) would suspend computer for user=${c.userId} after ${mins} min idle`);
        announced.add(c.userId);
        continue;
      }
      inFlight.add(c.userId);
      deps.log(`[idle] suspending computer for user=${c.userId}: no turns or viewers for ${mins} min`);
      try {
        await deps.suspend(c.userId);
      } catch (e: any) {
        deps.log(`[idle] suspend failed for user=${c.userId}: ${e?.message ?? e}`);
        busyAt.set(c.userId, deps.now()); // back off a full window before retrying
      } finally {
        inFlight.delete(c.userId);
      }
    }
    return out;
  }

  return { decide, sweep };
}

// ---------- production wiring ----------

export function startIdleWatcher() {
  const watcher = createIdleWatcher(
    {
      now: dbNow,
      computers: () => (db.prepare("SELECT user_id, status FROM computers WHERE orgo_id IS NOT NULL").all() as any[]).map((r) => ({ userId: r.user_id, status: r.status })),
      anyTurnRunning: (userId) => (db.prepare('SELECT id FROM bots WHERE user_id=?').all(userId) as any[]).some((b) => isBotBusy(b.id) || getLive(b.id).isRunning),
      viewers: viewerCount,
      lastActivity: (userId) => {
        const b = db.prepare('SELECT MAX(updated_at) t FROM bots WHERE user_id=?').get(userId) as any;
        const c = db.prepare('SELECT updated_at t FROM computers WHERE user_id=?').get(userId) as any;
        return Math.max(b?.t ?? 0, c?.t ?? 0);
      },
      suspend: (userId) => suspendComputer(userId, 'idle'),
      log: (m) => console.log(m),
    },
    { idleMs: idleConfig.idleMin * 60_000, dryRun: idleConfig.dryRun },
  );
  const timer = setInterval(() => { watcher.sweep().catch((e) => console.warn('[idle] sweep failed', e)); }, idleConfig.sweepSec * 1000);
  timer.unref?.();
  console.log(`[idle] hibernation after ${idleConfig.idleMin} min without turns or viewers${idleConfig.dryRun ? ' (dry run)' : ''}`);
  return { watcher, stop: () => clearInterval(timer) };
}
