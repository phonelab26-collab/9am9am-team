import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BotAvatarFor } from '../../avatar/BotAvatar';
import { displayName, useStore } from '../../store';
import { Icon } from '../Icon';
import { Modal } from './Modal';

export function SearchModal() {
  const bots = useStore((s) => s.bots);
  const setModal = useStore((s) => s.setModal);
  const selectBot = useStore((s) => s.selectBot);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setModal(null), [setModal]);

  const results = useMemo(() => {
    const ordered = [...bots].sort((a, b) => Number(b.isPrimary) - Number(a.isPrimary) || b.lastActivityAt - a.lastActivityAt);
    const needle = q.trim().toLowerCase();
    if (!needle) return ordered;
    return ordered.filter((b) => `${b.name} ${b.label} ${b.description}`.toLowerCase().includes(needle));
  }, [bots, q]);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const open = (id: string) => { selectBot(id); close(); };

  return (
    <Modal onClose={close} className="search-modal" label="Search">
      <div className="search-modal__field">
        <span className="search-modal__icon"><Icon name="search" size={18} opacity={0.56} /></span>
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search Bots, chats and settings"
          aria-label="Search"
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === 'Enter' && results[active]) { e.preventDefault(); open(results[active].id); }
          }}
        />
      </div>
      <div className="search-modal__results" ref={listRef} role="listbox">
        <div className="search-modal__section">Bots</div>
        {results.map((b, i) => (
          <button
            key={b.id}
            type="button"
            role="option"
            aria-selected={i === active}
            data-idx={i}
            className={`search-row${i === active ? ' is-active' : ''}`}
            onMouseMove={() => setActive(i)}
            onClick={() => open(b.id)}
          >
            <BotAvatarFor bot={b} size={28} />
            <span className="search-row__text">
              <span className="search-row__name">{displayName(b)}</span>
              <span className="search-row__sub">{b.lastPreview ?? (b.description || 'No messages yet')}</span>
            </span>
          </button>
        ))}
        {results.length === 0 && <div className="search-modal__empty">No Bots match “{q}”</div>}
      </div>
    </Modal>
  );
}
