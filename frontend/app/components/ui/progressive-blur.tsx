'use client';

import { Box } from '@radix-ui/themes';

const LAYERS = 6;
const BASE_BLUR = 0.4;

/**
 * Stacked masks so blur ramps instead of cutting a hard frosted edge.
 * A single backdrop-filter reads as a pane; doubling the radius per
 * layer matches how blur is perceived.
 */
export function ProgressiveBlur({
  height = 160,
  zIndex = 0,
  placement = 'bottom',
  scrim = 'var(--olive-1)',
}: {
  height?: number;
  zIndex?: number;
  placement?: 'top' | 'bottom';
  scrim?: string;
}) {
  const top = placement === 'top';
  const away = top ? 'to top' : 'to bottom';
  return (
    <Box
      aria-hidden
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: top ? 0 : undefined,
        bottom: top ? undefined : 0,
        height,
        zIndex,
        pointerEvents: 'none',
      }}
    >
      {Array.from({ length: LAYERS }, (_, i) => {
        const start = (i / LAYERS) * 100;
        const full = ((i + 1) / LAYERS) * 100;
        const mask = `linear-gradient(${away}, transparent ${start}%, black ${full}%)`;
        return (
          <Box
            key={i}
            style={{
              position: 'absolute',
              inset: 0,
              backdropFilter: `blur(${BASE_BLUR * 2 ** i}px)`,
              WebkitBackdropFilter: `blur(${BASE_BLUR * 2 ** i}px)`,
              maskImage: mask,
              WebkitMaskImage: mask,
            }}
          />
        );
      })}
      <Box
        style={{
          position: 'absolute',
          inset: 0,
          background: `linear-gradient(${away}, transparent 0%, ${scrim} 92%)`,
          opacity: 0.9,
        }}
      />
    </Box>
  );
}
