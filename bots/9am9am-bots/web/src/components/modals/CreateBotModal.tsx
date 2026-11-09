import { useCallback, useState } from 'react';
import type { AvatarColor, AvatarShape, Bot } from '@shared/types';
import { BotAvatar, PICKER_SHAPES } from '../../avatar/BotAvatar';
import { api } from '../../lib/api';
import { COLOR_ORDER, palette } from '../../lib/colors';
import { useStore } from '../../store';
import { Icon } from '../Icon';
import { Modal } from './Modal';

const ROWS = [PICKER_SHAPES.slice(0, 5), PICKER_SHAPES.slice(5, 10), PICKER_SHAPES.slice(10)];
const COLOR_ROWS = [COLOR_ORDER.slice(0, 6), COLOR_ORDER.slice(6)];

export function CreateBotModal() {
  const setModal = useStore((s) => s.setModal);
  const [name, setName] = useState('');
  const [shape, setShape] = useState<AvatarShape>('heart');
  const [color, setColor] = useState<AvatarColor>('orange');
  const [mode, setMode] = useState<Bot['mode']>('personal');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const close = useCallback(() => setModal(null), [setModal]);
  const canCreate = name.trim().length > 0 && !busy;

  const create = async () => {
    if (!canCreate) return;
    setBusy(true); setError(null);
    try {
      const { bot } = await api.createBot({ name: name.trim(), avatarShape: shape, avatarColor: color, mode });
      const st = useStore.getState();
      st.set({ bots: st.bots.some((b) => b.id === bot.id) ? st.bots : [...st.bots, bot] });
      st.selectBot(bot.id);
      close();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal onClose={close} className="create-modal" label="Create New Bot">
      <form onSubmit={(e) => { e.preventDefault(); create(); }}>
        <div className="modal__header">
          <div className="modal__title">Create New Bot</div>
          <button type="button" className="modal__close" onClick={close} aria-label="Close"><Icon name="close" size={14} opacity={0.68} /></button>
        </div>
        <div className="create-modal__body">
          <BotAvatar shape={shape} color={color} size={96} className="create-modal__preview" />
          <input
            className="text-input create-modal__name"
            placeholder="Give your Bot a name"
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            aria-label="Bot name"
          />
          <div className="shape-grid" role="radiogroup" aria-label="Character shape">
            {ROWS.map((row, i) => (
              <div key={i} className="shape-grid__row">
                {row.map((s) => (
                  <button key={s} type="button" role="radio" aria-checked={shape === s} aria-label={s} className="shape-cell" onClick={() => setShape(s)}>
                    <BotAvatar shape={s} color={color} size={36} ring={shape === s} />
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="swatches" role="radiogroup" aria-label="Character color">
            {COLOR_ROWS.map((row, i) => (
              <div key={i} className="swatches__row">
                {row.map((c) => (
                  <button key={c} type="button" role="radio" aria-checked={color === c} aria-label={c} className="swatch-cell" onClick={() => setColor(c)}>
                    <span className={`swatch${color === c ? ' is-selected' : ''}`} style={{ background: palette(c).fill }} />
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="mode-pick" role="radiogroup" aria-label="Bot type">
            <button type="button" role="radio" aria-checked={mode === 'personal'} className={`mode-pick__opt${mode === 'personal' ? ' is-active' : ''}`} onClick={() => setMode('personal')}>
              <span className="mode-pick__title">Personal assistant</span>
              <span className="mode-pick__sub">Works for you, with your apps</span>
            </button>
            <button type="button" role="radio" aria-checked={mode === 'teammate'} className={`mode-pick__opt${mode === 'teammate' ? ' is-active' : ''}`} onClick={() => setMode('teammate')}>
              <span className="mode-pick__title">Teammate</span>
              <span className="mode-pick__sub">Works with your whole team</span>
            </button>
          </div>
          {error && <div className="card__error">{error}</div>}
        </div>
        <div className="modal__footer">
          <button type="button" className="link-btn link-btn--small" style={{ marginRight: 'auto' }} onClick={() => setModal('group')}>New group chat instead</button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={close}>Cancel</button>
          <button type="submit" className="btn btn--primary btn--sm" disabled={!canCreate}>{busy ? 'Creating…' : 'Create'}</button>
        </div>
      </form>
    </Modal>
  );
}
