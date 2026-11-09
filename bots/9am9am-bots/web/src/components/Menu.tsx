import { useEffect, useRef, type ReactNode } from 'react';

export interface MenuItem { label: string; onSelect: () => void; danger?: boolean; hint?: string }

/** Small popover menu anchored by its parent (position: relative). Closes on outside click / Escape. */
export function Menu({ items, onClose, align = 'left', placement = 'below', children }: { items: MenuItem[]; onClose: () => void; align?: 'left' | 'right' | 'center'; placement?: 'below' | 'above'; children?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    const t = setTimeout(() => document.addEventListener('mousedown', down));
    document.addEventListener('keydown', key, true);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key, true); };
  }, [onClose]);
  return (
    <div ref={ref} className={`menu is-${align} is-${placement}`} role="menu">
      {children}
      {items.map((it) => (
        <button key={it.label} type="button" role="menuitem" className={`menu__item${it.danger ? ' is-danger' : ''}`} onClick={() => { onClose(); it.onSelect(); }}>
          <span>{it.label}</span>
          {it.hint && <span className="menu__hint">{it.hint}</span>}
        </button>
      ))}
    </div>
  );
}
