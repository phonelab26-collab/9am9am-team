import { create } from 'zustand';
import type { ServerEvent } from '@shared/types';
import { useStore } from './store';

/** UI state for bot settings, app settings, the Tasks view and the theme. Kept apart from store.ts on purpose. */

export type Theme = 'system' | 'light' | 'dark';
export type BotSettingsTab = 'general' | 'skills' | 'memories' | 'routines';
export type AppSection = 'general' | 'computer' | 'permissions' | 'apps' | 'account';

interface ConfigUI {
  botSettings: { botId: string; tab: BotSettingsTab } | null;
  appSettings: AppSection | null;
  view: 'chat' | 'tasks';
  theme: Theme;
  /** Bumped by server events (tasks, memories) so open views refetch. Keys: 'tasks', 'memories:<botId>'. */
  versions: Record<string, number>;
  openBotSettings(botId: string, tab?: BotSettingsTab): void;
  closeBotSettings(): void;
  openAppSettings(section?: AppSection): void;
  closeAppSettings(): void;
  setView(v: 'chat' | 'tasks'): void;
  setTheme(t: Theme): void;
  bump(key: string): void;
}

const LS_THEME = '9am9am.theme';
const readTheme = (): Theme => {
  try { const t = localStorage.getItem(LS_THEME); return t === 'light' || t === 'dark' ? t : 'system'; } catch { return 'system'; }
};

const darkQuery = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

export function applyTheme(t: Theme) {
  const resolved = t === 'system' ? (darkQuery?.matches ? 'dark' : 'light') : t;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

export const useConfigUI = create<ConfigUI>((set, get) => ({
  botSettings: null,
  appSettings: null,
  view: 'chat',
  theme: readTheme(),
  versions: {},
  openBotSettings: (botId, tab = 'general') => set({ botSettings: { botId, tab } }),
  closeBotSettings: () => set({ botSettings: null }),
  openAppSettings: (section = 'general') => set({ appSettings: section }),
  closeAppSettings: () => set({ appSettings: null }),
  setView: (view) => set({ view }),
  setTheme: (theme) => {
    try { localStorage.setItem(LS_THEME, theme); } catch { /* ignore */ }
    applyTheme(theme);
    set({ theme });
  },
  bump: (key) => set({ versions: { ...get().versions, [key]: (get().versions[key] ?? 0) + 1 } }),
}));

// Apply the saved theme as early as possible, and follow the OS while on "system".
applyTheme(readTheme());
darkQuery?.addEventListener?.('change', () => { if (useConfigUI.getState().theme === 'system') applyTheme('system'); });

// store.ts forwards events it doesn't handle itself as a DOM event.
window.addEventListener('9am9am:event', (e) => {
  const ev = (e as CustomEvent<ServerEvent>).detail;
  const { bump } = useConfigUI.getState();
  if (ev.type === 'tasks') { bump('tasks'); bump(`tasks:${ev.botId}`); }
  if (ev.type === 'memories') bump(`memories:${ev.botId}`);
});

// Picking a bot anywhere (sidebar, search) goes back to its chat.
useStore.subscribe((s, prev) => {
  if (s.selectedBotId !== prev.selectedBotId && useConfigUI.getState().view !== 'chat') useConfigUI.getState().setView('chat');
});

export const useVersion = (key: string) => useConfigUI((s) => s.versions[key] ?? 0);
