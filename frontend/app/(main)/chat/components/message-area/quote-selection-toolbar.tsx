'use client';

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useCommandStore } from '@/lib/store/command-store';
import { clipQuotedSelection, normalizeQuotedSelection } from '../../utils/quoted-selection';
import { SelectionActions } from './selection-actions';

interface QuoteSelectionLayerProps {
  enabled: boolean;
  /** The reader may send here: the bar offers Reply and Ask, not Copy alone. */
  canQuote: boolean;
  children: React.ReactNode;
}

interface ToolbarState {
  /** The words as selected: what Copy copies. */
  words: string;
  /** The excerpt the composer takes. */
  text: string;
  truncated: boolean;
  x: number;
  y: number;
}

function selectionInRoot(root: HTMLElement): ToolbarState | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return null;
  const raw = normalizeQuotedSelection(sel.toString());
  if (!raw) return null;
  const { text, truncated } = clipQuotedSelection(raw);
  if (!text) return null;
  const rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;
  return {
    words: sel.toString().trim(),
    text,
    truncated,
    x: rect.left + rect.width / 2,
    y: rect.top,
  };
}

/** Between the bar and the selection, and between the bar and the window's edges. */
const GAP_PX = 4;
const EDGE_PX = 8;

/**
 * Select prose in a finished answer, then Reply or Ask puts the excerpt on the composer as a chip (same thread).
 * Someone who may not send can copy it.
 */
export function QuoteSelectionLayer({ enabled, canQuote, children }: QuoteSelectionLayerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [toolbar, setToolbar] = useState<ToolbarState | null>(null);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  const hide = useCallback(() => setToolbar(null), []);

  const showFromSelection = useCallback(() => {
    const root = rootRef.current;
    if (!enabled || !root) {
      setToolbar(null);
      return;
    }
    const next = selectionInRoot(root);
    setToolbar((current) => {
      if (next) return next;
      const active = document.activeElement;
      if (
        current &&
        toolbarRef.current &&
        active instanceof Node &&
        toolbarRef.current.contains(active)
      ) {
        return current;
      }
      return null;
    });
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setToolbar(null);
      return;
    }
    const onPointerUp = (event: PointerEvent) => {
      const root = rootRef.current;
      if (!root) return;
      if (event.target instanceof Node && root.contains(event.target)) {
        requestAnimationFrame(showFromSelection);
        return;
      }
      hide();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hide();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key === 'Escape') return;
      requestAnimationFrame(showFromSelection);
    };
    const onScroll = () => hide();
    document.addEventListener('pointerup', onPointerUp);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('keyup', onKeyUp);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('pointerup', onPointerUp);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('keyup', onKeyUp);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, [enabled, hide, showFromSelection]);

  // Over the selection and centred on it, but always inside the window: one near a phone's side, or one that starts
  // above the screen.
  useLayoutEffect(() => {
    const bar = toolbarRef.current;
    if (!toolbar || !bar) return;
    const half = bar.offsetWidth / 2;
    setAt({
      top: Math.max(EDGE_PX, toolbar.y - bar.offsetHeight - GAP_PX),
      left: Math.max(EDGE_PX + half, Math.min(toolbar.x, window.innerWidth - EDGE_PX - half)),
    });
  }, [toolbar]);

  const handleQuote = () => {
    if (!toolbar) return;
    useCommandStore.getState().dispatch('showQuoteBar', {
      text: toolbar.text,
      truncated: toolbar.truncated,
    });
    window.getSelection()?.removeAllRanges();
    setToolbar(null);
  };
  const quote = canQuote ? handleQuote : undefined;

  return (
    <div ref={rootRef} data-quotable="" data-testid="answer-quotable">
      {children}
      {toolbar && (
        <SelectionActions
          ref={toolbarRef}
          data-testid="quote-selection-toolbar"
          text={toolbar.words}
          onReply={quote}
          onAsk={quote}
          style={{
            position: 'fixed',
            // Placed before the first paint (a layout effect), so it never shows at 0, 0.
            top: at?.top ?? 0,
            left: at?.left ?? 0,
            transform: 'translateX(-50%)',
            zIndex: 50,
          }}
        />
      )}
    </div>
  );
}
