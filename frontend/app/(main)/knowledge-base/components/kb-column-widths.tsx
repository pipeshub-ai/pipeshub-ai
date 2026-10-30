'use client';

import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { Box } from '@radix-ui/themes';

/** The fixed-width columns of the records table; File name takes the rest. */
export type KbColumn = 'status' | 'source' | 'size' | 'created' | 'updated';

const DEFAULT_WIDTHS: Record<KbColumn, number> = {
  status: 72,
  source: 72,
  size: 96,
  created: 147,
  updated: 146,
};
// Narrowest each column can be without its header or a value ("29 Oct 2026") wrapping.
const MIN_WIDTHS: Record<KbColumn, number> = {
  status: 64,
  source: 64,
  size: 80,
  created: 120,
  updated: 120,
};
const MAX_WIDTH = 480;
const STORAGE_KEY = 'pipeshub.kb.columnWidths';

/** Width of a column as a CSS value; falls back to the default outside a provider. */
export const kbCol = (column: KbColumn) => `var(--kb-col-${column}, ${DEFAULT_WIDTHS[column]}px)`;

/** Narrowest a column may shrink to when the table is narrower than the saved widths. */
export const kbColMin = (column: KbColumn) => `${MIN_WIDTHS[column]}px`;

const clamp = (column: KbColumn, n: number) =>
  Math.min(MAX_WIDTH, Math.max(MIN_WIDTHS[column], Math.round(n)));

function readStored(): Record<KbColumn, number> {
  if (typeof window === 'undefined') return DEFAULT_WIDTHS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Partial<Record<KbColumn, unknown>>) : {};
    const out = { ...DEFAULT_WIDTHS };
    (Object.keys(DEFAULT_WIDTHS) as KbColumn[]).forEach((c) => {
      const v = parsed[c];
      if (typeof v === 'number' && Number.isFinite(v)) out[c] = clamp(c, v);
    });
    return out;
  } catch {
    return DEFAULT_WIDTHS;
  }
}

/**
 * Column widths for the records table (All records and Collections), saved per browser.
 * Spread `style` on an element wrapping the header and the rows; cells read `kbCol(...)`.
 */
export function useKbColumnWidths() {
  const [widths, setWidths] = useState<Record<KbColumn, number>>(DEFAULT_WIDTHS);
  const widthsRef = useRef(widths);
  widthsRef.current = widths;

  // Read before paint (no jump from the defaults) but after hydration, so server and client agree
  useLayoutEffect(() => setWidths(readStored()), []);

  const save = (next: Record<KbColumn, number>) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Storage full or blocked: widths still apply for this visit
    }
  };

  /**
   * Drag the boundary between `left` and `right`. 'name' on the left means the flexible
   * File name column; `right` null means the last fixed column, before the actions.
   */
  const startResize = useCallback(
    (e: React.PointerEvent, left: KbColumn | 'name', right: KbColumn | null) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const start = widthsRef.current;
      // The last widths of this drag; the ref only catches up on the next render
      let latest = start;
      const onMove = (ev: PointerEvent) => {
        const dx = ev.clientX - startX;
        const next = { ...start };
        if (left === 'name' && right) {
          next[right] = clamp(right, start[right] - dx);
        } else if (left !== 'name' && !right) {
          next[left] = clamp(left, start[left] + dx);
        } else if (left !== 'name' && right) {
          // Only the boundary moves: what one column gains the other gives up
          const total = start[left] + start[right];
          const l = clamp(left, Math.min(start[left] + dx, total - MIN_WIDTHS[right]));
          next[left] = l;
          next[right] = clamp(right, total - l);
        }
        latest = next;
        setWidths(next);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        document.body.style.removeProperty('cursor');
        document.body.style.removeProperty('user-select');
        if (latest !== start) save(latest);
      };
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      // A cancelled pointer (touch / pen gesture, OS interrupt) ends the drag too
      window.addEventListener('pointercancel', onUp);
    },
    []
  );

  const reset = useCallback((columns: KbColumn[]) => {
    const next = { ...widthsRef.current };
    columns.forEach((c) => (next[c] = DEFAULT_WIDTHS[c]));
    setWidths(next);
    save(next);
  }, []);

  const style = Object.fromEntries(
    (Object.keys(widths) as KbColumn[]).map((c) => [`--kb-col-${c}`, `${widths[c]}px`])
  ) as React.CSSProperties;

  return { style, startResize, reset };
}

/**
 * Zero-width drag target on a header boundary, so it never shifts the columns.
 * Double-click puts the columns either side back to their default widths.
 */
export function ColumnResizeHandle({
  label,
  onPointerDown,
  onReset,
}: {
  label: string;
  onPointerDown: (e: React.PointerEvent) => void;
  onReset: () => void;
}) {
  return (
    <Box style={{ position: 'relative', width: 0, alignSelf: 'stretch', flexShrink: 0 }}>
      <Box
        role="separator"
        aria-orientation="vertical"
        aria-label={label}
        className="kb-col-resize"
        onPointerDown={onPointerDown}
        onDoubleClick={onReset}
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: -4,
          width: 8,
          zIndex: 1,
          cursor: 'col-resize',
          touchAction: 'none',
        }}
      />
    </Box>
  );
}
