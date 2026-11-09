import type { CSSProperties } from 'react';

const PATHS = {
  trash: 'M9 3h6a1 1 0 0 1 1 1v1h4a1 1 0 1 1 0 2h-1.06l-.85 12.1A2 2 0 0 1 16.1 21H7.9a2 2 0 0 1-2-1.9L5.06 7H4a1 1 0 0 1 0-2h4V4a1 1 0 0 1 1-1Zm-1.93 4 .83 12h8.2l.83-12H7.07ZM10 10a1 1 0 0 1 1 1v5a1 1 0 1 1-2 0v-5a1 1 0 0 1 1-1Zm4 0a1 1 0 0 1 1 1v5a1 1 0 1 1-2 0v-5a1 1 0 0 1 1-1Z',
  download: 'M12 3a1 1 0 0 1 1 1v9.59l3.3-3.3a1 1 0 1 1 1.4 1.42l-5 5a1 1 0 0 1-1.4 0l-5-5a1 1 0 1 1 1.4-1.42l3.3 3.3V4a1 1 0 0 1 1-1ZM5 19a1 1 0 1 0 0 2h14a1 1 0 1 0 0-2H5Z',
  folder: 'M3 6.5A2.5 2.5 0 0 1 5.5 4h4.09a2 2 0 0 1 1.41.59L12.41 6h6.09A2.5 2.5 0 0 1 21 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-11Z',
  doc: 'M7 3h7.59a1 1 0 0 1 .7.3l4.42 4.4a1 1 0 0 1 .29.71V19a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm0 2v14h11V9.41L14.59 6H7V5Zm2 7h6a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2Zm0 3.5h4a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2Z',
  gear: 'M10.6 2.5h2.8l.5 2.4c.6.2 1.1.5 1.6.9l2.3-.8 1.4 2.4-1.8 1.6c.1.6.1 1.2 0 1.8l1.8 1.6-1.4 2.4-2.3-.8c-.5.4-1 .7-1.6.9l-.5 2.4h-2.8l-.5-2.4c-.6-.2-1.1-.5-1.6-.9l-2.3.8-1.4-2.4 1.8-1.6a5.6 5.6 0 0 1 0-1.8L4.8 7.4l1.4-2.4 2.3.8c.5-.4 1-.7 1.6-.9l.5-2.4ZM12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z',
  play: 'M8 5.6v12.8a1 1 0 0 0 1.5.86l10.2-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6Z',
  check_circle: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Zm4.2 6.3a1 1 0 0 0-1.4 0L11 13.08l-1.8-1.8a1 1 0 1 0-1.4 1.42l2.5 2.5a1 1 0 0 0 1.4 0l4.5-4.5a1 1 0 0 0 0-1.4Z',
  spark: 'M12 2.5c.4 0 .7.3.8.6l1.2 4.3a3 3 0 0 0 2 2l4.3 1.3a.8.8 0 0 1 0 1.6L16 13.5a3 3 0 0 0-2 2l-1.3 4.3a.8.8 0 0 1-1.6 0L10 15.5a3 3 0 0 0-2-2l-4.3-1.2a.8.8 0 0 1 0-1.6L8 9.4a3 3 0 0 0 2-2l1.2-4.3c.1-.3.4-.6.8-.6Z',
  arrow_left: 'M10.7 5.3a1 1 0 0 1 0 1.4L6.41 11H19a1 1 0 1 1 0 2H6.41l4.3 4.3a1 1 0 0 1-1.42 1.4l-6-6a1 1 0 0 1 0-1.4l6-6a1 1 0 0 1 1.42 0Z',
  tasks: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm0 2v12h14V6H5Zm2.3 3.3a1 1 0 0 1 1.4 0l.8.8 1.8-1.8a1 1 0 1 1 1.4 1.4l-2.5 2.5a1 1 0 0 1-1.4 0l-1.5-1.5a1 1 0 0 1 0-1.4ZM14 9.5h3a1 1 0 1 1 0 2h-3a1 1 0 1 1 0-2Zm-6 5h9a1 1 0 1 1 0 2H8a1 1 0 1 1 0-2Z',
} as const;

export type GlyphName = keyof typeof PATHS;

export function Glyph({ name, size = 16, opacity, style, className }: { name: GlyphName; size?: number; opacity?: number; style?: CSSProperties; className?: string }) {
  if (name === 'gear') {
    // Outline gear reads better than a filled one at 16px.
    return (
      <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden style={{ opacity, flexShrink: 0, ...style }} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    );
  }
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden style={{ opacity, flexShrink: 0, ...style }}>
      <path d={PATHS[name]} fill="currentColor" fillRule="evenodd" />
    </svg>
  );
}
