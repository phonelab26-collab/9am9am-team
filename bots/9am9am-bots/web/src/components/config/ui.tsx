import { useEffect, useState, type ReactNode } from 'react';
import '../../styles/config.css';

export function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={`toggle${on ? ' is-on' : ''}`} onClick={() => onChange(!on)}>
      <span className="toggle__knob" />
    </button>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="cfg-field">
      <label className="cfg-field__label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <div className="cfg-field__hint">{hint}</div>}
    </div>
  );
}

/** A small segmented control with optional per-option hints. */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="cfg-seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} className={value === o.value ? 'is-active' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Button that asks "Sure?" in place before running a destructive action. */
export function ConfirmButton({ children, confirmLabel = 'Delete', onConfirm, className = 'cfg-btn cfg-btn--ghost-danger', disabled, title }: { children: ReactNode; confirmLabel?: string; onConfirm: () => Promise<unknown> | void; className?: string; disabled?: boolean; title?: string }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!asking) return;
    const t = setTimeout(() => setAsking(false), 4000);
    return () => clearTimeout(t);
  }, [asking]);
  if (!asking) return <button type="button" className={className} disabled={disabled} title={title} onClick={() => setAsking(true)}>{children}</button>;
  return (
    <button
      type="button"
      className="cfg-btn cfg-btn--danger"
      disabled={busy}
      onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); setAsking(false); } }}
    >
      {busy ? 'Working…' : confirmLabel}
    </button>
  );
}

export function shortDate(ts: number): string {
  const d = new Date(ts);
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString('en-US', opts);
}

/** "Next today 8:00 AM", shown in the routine's own time zone so it matches its schedule text. */
export function nextRunLabel(ts: number | null, timeZone?: string): string {
  if (!ts) return '';
  let tz = timeZone || undefined;
  try { new Intl.DateTimeFormat('en-US', { timeZone: tz }); } catch { tz = undefined; }
  const dayKey = (t: number) => new Date(t).toLocaleDateString('en-US', { timeZone: tz });
  const day = new Date(ts).toLocaleDateString('en-US', { weekday: 'short', timeZone: tz });
  const time = new Date(ts).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz });
  const rel = dayKey(ts) === dayKey(Date.now()) ? 'today' : dayKey(ts) === dayKey(Date.now() + 86_400_000) ? 'tomorrow' : day;
  return `Next ${rel} ${time}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
