import type { AvatarColor } from '@shared/types';

/**
 * Bot colors. `fill` is the avatar/swatch color, `bubble` the user message bubble, `action` the
 * accent for toggles and the send button, and `hover` its hover state.
 */
export interface BotPalette { fill: string; bubble: string; action: string; hover: string }

export const BOT_COLORS: Record<AvatarColor, BotPalette> = {
  // Black follows the theme (ink on light, light ink on dark), so it uses tokens instead of hex.
  black: { fill: '#000000', bubble: 'var(--color-bubble-user)', action: 'var(--color-primary)', hover: 'var(--color-bg-primary-hover)' },
  brown: { fill: '#A06B39', bubble: '#86592E', action: '#9D6937', hover: '#86592E' },
  red: { fill: '#FB2B3E', bubble: '#C82532', action: '#E72E3B', hover: '#C82532' },
  orange: { fill: '#FD690F', bubble: '#D2550A', action: '#EE620D', hover: '#D2550A' },
  yellow: { fill: '#F59312', bubble: '#C2730B', action: '#DA830E', hover: '#C2730B' },
  green: { fill: '#14C16F', bubble: '#0D9655', action: '#10AB61', hover: '#0D9655' },
  cyan: { fill: '#13B7A2', bubble: '#0D9381', action: '#10A693', hover: '#0D9381' },
  blue: { fill: '#1E86FC', bubble: '#126FD4', action: '#1680F3', hover: '#126FD4' },
  violet: { fill: '#8F59F9', bubble: '#7442D1', action: '#8351E4', hover: '#7442D1' },
  magenta: { fill: '#F93498', bubble: '#C82779', action: '#E52F8B', hover: '#C82779' },
  gray: { fill: '#959595', bubble: '#6E6E6E', action: '#7A7A7A', hover: '#6E6E6E' },
};

/** Swatch order in the Create New Bot picker. */
export const COLOR_ORDER: AvatarColor[] = ['black', 'brown', 'red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'violet', 'magenta', 'gray'];

export const palette = (c: AvatarColor | string | undefined): BotPalette => BOT_COLORS[(c as AvatarColor) ?? 'black'] ?? BOT_COLORS.black;

/** CSS custom properties that theme a subtree with a bot's color. */
export function botVars(c: AvatarColor | string | undefined): React.CSSProperties {
  const p = palette(c);
  return {
    ['--bot-fill' as string]: p.fill, ['--bot-bubble' as string]: p.bubble, ['--bot-action' as string]: p.action, ['--bot-action-hover' as string]: p.hover,
    ['--bot-on-action' as string]: p === BOT_COLORS.black ? 'var(--color-on-primary)' : '#FFFFFF',
    ['--bot-action-soft' as string]: `color-mix(in srgb, ${p.action} 13%, transparent)`,
    ['--bot-action-text' as string]: p === BOT_COLORS.black ? 'var(--color-text-secondary)' : p.action,
  };
}
