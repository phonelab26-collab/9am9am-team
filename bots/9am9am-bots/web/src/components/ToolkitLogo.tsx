import { useState } from 'react';

/** App logo tile (40px in Connect Apps) with a first-letter fallback. */
export function ToolkitLogo({ name, logo, size = 40, radius = 8 }: { name: string; logo?: string; size?: number; radius?: number }) {
  const [broken, setBroken] = useState(false);
  const letter = (name.trim()[0] ?? '?').toUpperCase();
  return (
    <span className="toolkit-logo" style={{ width: size, height: size, borderRadius: radius }}>
      {logo && !broken ? (
        <img src={logo} alt="" width={size * 0.7} height={size * 0.7} onError={() => setBroken(true)} draggable={false} />
      ) : (
        <span className="toolkit-logo__letter" style={{ fontSize: size * 0.42 }}>{letter}</span>
      )}
    </span>
  );
}
