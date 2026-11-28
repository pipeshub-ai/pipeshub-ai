'use client';

import { Box } from '@radix-ui/themes';

/**
 * The fade at a pane's edge: content dissolves into the surface instead of meeting a hard line. A plain eased
 * gradient, not stacked backdrop blurs: those re-blur the band every frame the content under it moves (a streaming
 * answer, a scroll), which halved the chat's frame rate.
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
  const away = placement === 'top' ? 'to top' : 'to bottom';
  const at = (share: number) => `color-mix(in srgb, ${scrim} ${share}%, transparent)`;
  return (
    <Box
      aria-hidden
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: placement === 'top' ? 0 : undefined,
        bottom: placement === 'top' ? undefined : 0,
        height,
        zIndex,
        pointerEvents: 'none',
        // Eased, so the ramp has no visible start line.
        background: `linear-gradient(${away}, transparent 0%, ${at(12)} 25%, ${at(45)} 50%, ${at(82)} 75%, ${scrim} 100%)`,
      }}
    />
  );
}
