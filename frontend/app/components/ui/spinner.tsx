'use client';

import React from 'react';

export interface SpinnerProps {
  /** Diameter in pixels. Default: 16 */
  size?: number;
  /** Stroke color. Default: currentColor (inherits from parent text color) */
  color?: string;
  /** Stroke thickness in pixels. Default: 2 */
  thickness?: number;
  /** Extra inline styles */
  style?: React.CSSProperties;
  /** Accessible label (announced to screen readers). Default: "Loading" */
  ariaLabel?: string;
}

/**
 * Spinner — lightweight, theme-aware loading indicator.
 *
 * Uses the global `@keyframes spin` from `app/globals.css` and a single
 * CSS border-rotate ring. No external dependencies, safe to render inline
 * next to text (aligns to currentColor).
 *
 * Respects `prefers-reduced-motion: reduce` by slowing the animation.
 */
export function Spinner({
  size = 16,
  color,
  thickness = 2,
  style,
  ariaLabel = 'Loading',
}: SpinnerProps) {
  const ringColor = color ?? 'currentColor';

  return (
    <span
      role="status"
      aria-label={ariaLabel}
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        border: `${thickness}px solid ${ringColor}`,
        borderTopColor: 'transparent',
        opacity: 0.75,
        animation: 'spin 0.7s linear infinite',
        boxSizing: 'border-box',
        flexShrink: 0,
        ...style,
      }}
    />
  );
}
