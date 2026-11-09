import { useEffect, useState } from 'react';
import type { Bot } from '@shared/types';
import { useStore } from '../store';
import { voiceApi, type VoiceOptions } from '../voice/api';
import { endCall, isCallActive, previewVoice, startCall, useVoice } from '../voice/call';
import { Icon } from './Icon';

const FALLBACK: VoiceOptions = {
  voices: ['ara', 'eve', 'leo', 'luna', 'rex', 'sal'].map((id) => ({ id, name: id[0].toUpperCase() + id.slice(1) })),
  languages: [{ code: 'auto', name: 'Auto-detect' }, { code: 'en', name: 'English' }],
  speeds: [0.7, 0.85, 1, 1.15, 1.3, 1.5],
};

const speedLabel = (s: number) => `${Number(s.toFixed(2))}x`;

function NativeSelect<T extends string | number>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  const current = options.find((o) => o.value === value)?.label ?? String(value);
  return (
    <span className="mini-select mini-select--native">
      <span>{current}</span>
      <Icon name="chev_down" size={10} opacity={0.66} />
      <select
        aria-label={label}
        value={String(value)}
        onChange={(e) => {
          const o = options.find((x) => String(x.value) === e.target.value);
          if (o) onChange(o.value);
        }}
      >
        {options.map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
      </select>
    </span>
  );
}

/** "Voice chat" section of the details panel: voice, speed, language (saved per bot), preview, start a call. */
export function VoiceSettings({ bot }: { bot: Bot }) {
  const [opts, setOpts] = useState<VoiceOptions>(FALLBACK);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inCall = useVoice((s) => isCallActive(s) && s.botId === bot.id);

  useEffect(() => { voiceApi.options().then(setOpts).catch(() => {}); }, []);
  useEffect(() => setError(null), [bot.id]);

  const save = async (patch: Partial<Pick<Bot, 'voice' | 'voiceSpeed' | 'voiceLanguage'>>) => {
    const prev = useStore.getState().bots.find((b) => b.id === bot.id);
    // Optimistic; the server's `bot` event confirms.
    useStore.setState((s) => ({ bots: s.bots.map((b) => (b.id === bot.id ? { ...b, ...patch } : b)) }));
    setError(null);
    try {
      const res = await fetch(`/api/bots/${bot.id}`, { method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch {
      if (prev) useStore.setState((s) => ({ bots: s.bots.map((b) => (b.id === bot.id ? { ...b, voice: prev.voice, voiceSpeed: prev.voiceSpeed, voiceLanguage: prev.voiceLanguage } : b)) }));
      setError('Couldn’t save that setting.');
    }
  };

  const preview = async () => {
    if (previewing) return;
    setPreviewing(true); setError(null);
    try { await previewVoice({ botId: bot.id, voice: bot.voice, speed: bot.voiceSpeed, language: bot.voiceLanguage }); }
    catch (e) { setError((e as Error).message || 'Preview unavailable'); }
    finally { setPreviewing(false); }
  };

  const voices = opts.voices.some((v) => v.id === bot.voice) ? opts.voices : [{ id: bot.voice, name: bot.voice }, ...opts.voices];
  const speeds = opts.speeds.includes(bot.voiceSpeed) ? opts.speeds : [...opts.speeds, bot.voiceSpeed].sort((a, b) => a - b);

  return (
    <div className="panel-card">
      <div className="panel-row panel-row--setting">
        <span>Voice</span>
        <span className="voice-setting__right">
          <button type="button" className="voice-preview" onClick={preview} disabled={previewing} title={`Hear ${bot.name}'s voice`}>{previewing ? 'Playing…' : 'Preview'}</button>
          <NativeSelect label="Voice" value={bot.voice} options={voices.map((v) => ({ value: v.id, label: v.name }))} onChange={(voice) => save({ voice })} />
        </span>
      </div>
      <div className="panel-row panel-row--setting">
        <span>Speed</span>
        <NativeSelect label="Speed" value={bot.voiceSpeed} options={speeds.map((s) => ({ value: s, label: speedLabel(s) }))} onChange={(voiceSpeed) => save({ voiceSpeed })} />
      </div>
      <div className="panel-row panel-row--setting">
        <span>Language</span>
        <NativeSelect label="Language" value={bot.voiceLanguage} options={opts.languages.map((l) => ({ value: l.code, label: l.name }))} onChange={(voiceLanguage) => save({ voiceLanguage })} />
      </div>
      <button type="button" className={`panel-row panel-row--action panel-row--call${inCall ? ' is-active' : ''}`} onClick={() => (inCall ? endCall() : startCall(bot.id))}>
        <Icon name="voice_a" size={14} opacity={inCall ? 1 : 0.56} /> {inCall ? 'End voice chat' : 'Start voice chat'}
      </button>
      {error && <div className="voice-error">{error}</div>}
    </div>
  );
}
