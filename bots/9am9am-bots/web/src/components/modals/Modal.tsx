import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** 50% black scrim + centered dialog. Escape and scrim click close. Focus is trapped loosely (restored on close). */
export function Modal({ onClose, children, className, label, top }: { onClose: () => void; children: ReactNode; className?: string; label: string; top?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', k);
    const t = setTimeout(() => {
      const el = ref.current?.querySelector<HTMLElement>('[autofocus], input, textarea, button');
      el?.focus();
    });
    return () => { window.removeEventListener('keydown', k); clearTimeout(t); prev?.focus?.(); };
  }, [onClose]);
  return createPortal(
    <div className="modal-root">
      <div className="modal-scrim" onMouseDown={onClose} />
      <div ref={ref} className={`modal ${className ?? ''}`} role="dialog" aria-modal="true" aria-label={label} style={top !== undefined ? { marginTop: top } : undefined}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
