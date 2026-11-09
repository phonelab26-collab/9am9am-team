// Composer dictation: hold the mic to talk (or tap to start, tap again to stop). Records with MediaRecorder,
// transcribes through the server's STT proxy, and hands the text back to the composer.
import { useCallback, useEffect, useRef, useState } from 'react';
import { voiceApi } from './api';
import { micErrorMessage, openMic } from './audio';

export type DictationState = 'idle' | 'recording' | 'transcribing';

const HOLD_MS = 350;
const MAX_MS = 120_000;

function pickMime(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  for (const m of ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/mp4']) if (MediaRecorder.isTypeSupported?.(m)) return m;
  return undefined;
}

export function useDictation(onText: (text: string) => void, onError: (msg: string) => void, language?: string) {
  const [state, setState] = useState<DictationState>('idle');
  const rec = useRef<{ mr: MediaRecorder; stream: MediaStream; chunks: Blob[]; downAt: number; cancelled: boolean; timer: ReturnType<typeof setTimeout> } | null>(null);
  const starting = useRef(false);
  const toggleMode = useRef(false);
  const cb = useRef({ onText, onError, language });
  cb.current = { onText, onError, language };

  const finish = useCallback((cancel = false) => {
    const r = rec.current;
    if (!r) return;
    r.cancelled = cancel;
    clearTimeout(r.timer);
    if (r.mr.state !== 'inactive') r.mr.stop();
    else r.stream.getTracks().forEach((t) => t.stop());
  }, []);

  const begin = useCallback(async () => {
    if (rec.current || starting.current) return;
    if (typeof MediaRecorder === 'undefined') { cb.current.onError('Dictation isn’t supported in this browser.'); return; }
    starting.current = true;
    const downAt = Date.now();
    try {
      const stream = await openMic();
      const mime = pickMime();
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const r = { mr, stream, chunks: [] as Blob[], downAt, cancelled: false, timer: setTimeout(() => finish(), MAX_MS) };
      rec.current = r;
      mr.ondataavailable = (e) => { if (e.data.size) r.chunks.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        rec.current = null;
        toggleMode.current = false;
        if (r.cancelled || !r.chunks.length || Date.now() - r.downAt < 250) { setState('idle'); return; }
        setState('transcribing');
        try {
          const text = await voiceApi.transcribe(new Blob(r.chunks, { type: mr.mimeType || mime || 'audio/webm' }), cb.current.language);
          if (text) cb.current.onText(text);
        } catch (e) {
          cb.current.onError((e as Error).message || 'Transcription failed');
        } finally {
          setState('idle');
        }
      };
      mr.start(250);
      setState('recording');
    } catch (e) {
      cb.current.onError(micErrorMessage(e));
      setState('idle');
    } finally {
      starting.current = false;
    }
  }, [finish]);

  // Pointer handlers: press-and-hold records until release; a quick tap toggles recording on, tap again stops.
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* ignore */ }
    if (rec.current && toggleMode.current) { finish(); return; }
    void begin();
  }, [begin, finish]);

  const onPointerUp = useCallback(() => {
    const r = rec.current;
    if (!r) {
      // Still waiting on the permission prompt: treat as a tap.
      if (starting.current) toggleMode.current = true;
      return;
    }
    if (toggleMode.current) return;
    if (Date.now() - r.downAt < HOLD_MS) toggleMode.current = true; // tap: keep recording
    else finish();
  }, [finish]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    if (rec.current) finish();
    else { toggleMode.current = true; void begin(); }
  }, [begin, finish]);

  // Esc cancels; unmount stops.
  useEffect(() => {
    if (state !== 'recording') return;
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(true); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [state, finish]);
  useEffect(() => () => finish(true), [finish]);

  return { state, onPointerDown, onPointerUp, onKeyDown, cancel: () => finish(true) };
}
