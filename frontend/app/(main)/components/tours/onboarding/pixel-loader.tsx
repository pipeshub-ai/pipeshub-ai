'use client';

import React from 'react';

// ===============================
// PixelLoader
// ===============================
// A dot-matrix progress bar styled as a pixel-art bar.
// Layout: columns of 6 dots (1×1 px each, 1px vertical gap), 1px wide columns with 2px horizontal gap.
// The first `percentage`% of columns render in green; the rest render in a dim white.

interface PixelLoaderProps {
  /** 0–100 */
  percentage: number;
  /** Total width of the loader in px (default: 181) */
  width?: number;
}

const DOT_SIZE = 1;        // px — width & height of each dot
const DOT_GAP_V = 1;       // px — vertical gap between dots within a column
const DOT_GAP_H = 2;       // px — horizontal gap between columns
const DOTS_PER_COLUMN = 6; // fixed at 6 rows

/** Total height of the loader (6 dots + 5 gaps between them) */
const LOADER_HEIGHT = DOTS_PER_COLUMN * DOT_SIZE + (DOTS_PER_COLUMN - 1) * DOT_GAP_V; // = 11px

const COLOR_ACTIVE = 'var(--accent-9)';
const COLOR_INACTIVE = 'rgba(255, 255, 255, 0.18)';

export function PixelLoader({ percentage, width = 181 }: PixelLoaderProps) {
  const columnWidth = DOT_SIZE + DOT_GAP_H; // 3px per column unit
  const totalColumns = Math.floor(width / columnWidth);
  const filledColumns = Math.round((Math.min(100, Math.max(0, percentage)) / 100) * totalColumns);

  return (
    <div
      aria-label={`Progress: ${Math.round(percentage)}%`}
      role="progressbar"
      aria-valuenow={Math.round(percentage)}
      aria-valuemin={0}
      aria-valuemax={100}
      style={{
        width: `${width}px`,
        height: `${LOADER_HEIGHT}px`,
        display: 'flex',
        alignItems: 'stretch',
        gap: `${DOT_GAP_H}px`,
        flexShrink: 0,
      }}
    >
      {Array.from({ length: totalColumns }).map((_, colIdx) => {
        const isActive = colIdx < filledColumns;
        return (
          <div
            key={colIdx}
            style={{
              width: `${DOT_SIZE}px`,
              flexShrink: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: `${DOT_GAP_V}px`,
            }}
          >
            {Array.from({ length: DOTS_PER_COLUMN }).map((_, rowIdx) => (
              <div
                key={rowIdx}
                style={{
                  width: `${DOT_SIZE}px`,
                  height: `${DOT_SIZE}px`,
                  borderRadius: '50%',
                  backgroundColor: isActive ? COLOR_ACTIVE : COLOR_INACTIVE,
                  flexShrink: 0,
                }}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}
