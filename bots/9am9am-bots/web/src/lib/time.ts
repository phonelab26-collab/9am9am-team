const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

export const clock = (ts: number) => new Date(ts).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

/** "Fri, Nov 6 3:56 PM" / "Yesterday 11:24 PM" / "Today 9:02 AM" dividers, like screen 01. */
export function dividerLabel(ts: number, now = Date.now()): string {
  const d = new Date(ts);
  const today = new Date(now);
  const y = new Date(now - 86_400_000);
  if (sameDay(d, today)) return `Today ${clock(ts)}`;
  if (sameDay(d, y)) return `Yesterday ${clock(ts)}`;
  const opts: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' };
  if (d.getFullYear() !== today.getFullYear()) opts.year = 'numeric';
  return `${d.toLocaleDateString('en-US', opts)} ${clock(ts)}`;
}

/** Compact elapsed: "12s", "3m", "1h 4m". */
export function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function relativeShort(ts: number, now = Date.now()): string {
  const s = (now - ts) / 1000;
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 172800) return 'Yesterday';
  return `${Math.floor(s / 86400)}d`;
}
