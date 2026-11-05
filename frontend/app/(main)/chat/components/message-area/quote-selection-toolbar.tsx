'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import { useCommandStore } from '@/lib/store/command-store';
import { clipQuotedSelection, normalizeQuotedSelection } from '../../utils/quoted-selection';

interface QuoteSelectionLayerProps {
  enabled: boolean;
  children: React.ReactNode;
}

interface ToolbarState {
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
    text,
    truncated,
    x: rect.left + rect.width / 2,
    y: rect.top,
  };
}

/**
 * ChatGPT-style quote: select prose in a finished answer, then Quote
 * puts the excerpt on the composer as a chip (same thread).
 */
export function QuoteSelectionLayer({ enabled, children }: QuoteSelectionLayerProps) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const [toolbar, setToolbar] = useState<ToolbarState | null>(null);

  const hide = useCallback(() => setToolbar(null), []);

  const showFromSelection = useCallback(() => {
    const root = rootRef.current;
    if (!enabled || !root) {
      setToolbar(null);
      return;
    }
    setToolbar(selectionInRoot(root));
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

  const handleQuote = (event: React.MouseEvent | React.PointerEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!toolbar) return;
    useCommandStore.getState().dispatch('showQuoteBar', {
      text: toolbar.text,
      truncated: toolbar.truncated,
    });
    window.getSelection()?.removeAllRanges();
    setToolbar(null);
  };

  return (
    <div ref={rootRef} data-quotable="" data-testid="answer-quotable">
      {children}
      {toolbar && (
        <Flex
          data-testid="quote-selection-toolbar"
          align="center"
          gap="1"
          role="toolbar"
          aria-label={t('chat.quoteSelection', 'Quote')}
          onMouseDown={(event) => event.preventDefault()}
          style={{
            position: 'fixed',
            left: toolbar.x,
            top: Math.max(8, toolbar.y - 8),
            transform: 'translate(-50%, -100%)',
            zIndex: 50,
            padding: '4px 8px',
            minHeight: 44,
            borderRadius: 'var(--radius-3)',
            background: 'var(--color-panel-solid)',
            border: '1px solid var(--olive-6)',
            boxShadow: 'var(--shadow-3)',
          }}
        >
          <button
            type="button"
            data-testid="quote-selection-button"
            onClick={handleQuote}
            aria-label={t('chat.quoteSelectionHint', 'Ask about this')}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              minHeight: 36,
              padding: '0 10px',
              border: 'none',
              borderRadius: 'var(--radius-2)',
              background: 'var(--olive-3)',
              color: 'var(--slate-12)',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            <MaterialIcon name="format_quote" size={18} color="var(--slate-11)" />
            <Text size="2" weight="medium">
              {t('chat.quoteSelection', 'Quote')}
            </Text>
          </button>
        </Flex>
      )}
    </div>
  );
}
