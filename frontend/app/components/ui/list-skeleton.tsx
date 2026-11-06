'use client';

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, VisuallyHidden } from '@radix-ui/themes';
import { LOADING_SHOW_DELAY_MS } from './use-deferred-loading';

/**
 * The one loading skeleton for lists.
 *
 * A bar is 10px (8px for paragraph previews), on the --fill-active role with
 * the smallest radius, and carries the shared `.skeleton-shimmer` sweep (static
 * under reduced motion). Every bar on screen sweeps in phase. Radix's own
 * Skeleton is not used: it pulses and forces its own colour.
 *
 * Only the list body is a skeleton; the real header, toolbar and filters stay.
 * Rows take the real row's height, so nothing moves when the rows arrive.
 *
 * Accessibility: the region is aria-busy, the bars are hidden from assistive
 * technology, and one visually hidden polite status names what is loading.
 * When an ancestor already announces the load, no second status is added.
 */

/** 10px: a list text line. */
export const SKELETON_BAR_HEIGHT = 'calc(var(--space-2) + var(--space-1) / 2)';
/** 8px: a paragraph preview line. */
export const SKELETON_PARAGRAPH_HEIGHT = 'var(--space-2)';

export type SkeletonBarSize = 'line' | 'paragraph';

interface SkeletonBarProps {
  /** CSS width (px, %, calc). */
  width: string | number;
  size?: SkeletonBarSize;
  style?: React.CSSProperties;
}

/** One placeholder bar. */
export function SkeletonBar({ width, size = 'line', style }: SkeletonBarProps) {
  return (
    <Box
      className="skeleton-shimmer"
      style={{
        width,
        maxWidth: '100%',
        height: size === 'paragraph' ? SKELETON_PARAGRAPH_HEIGHT : SKELETON_BAR_HEIGHT,
        borderRadius: 'var(--radius-1)',
        backgroundColor: 'var(--fill-active)',
        flexShrink: 0,
        ...style,
      }}
    />
  );
}

interface SkeletonGlyphProps {
  /** The real glyph's size in px (14, 16, 32). */
  size: number;
  shape?: 'square' | 'circle';
}

/** A glyph or avatar slot: a square (or circle for avatars) of the real glyph size. */
export function SkeletonGlyph({ size, shape = 'square' }: SkeletonGlyphProps) {
  return (
    <SkeletonBar
      width={size}
      style={{ height: size, borderRadius: shape === 'circle' ? 'var(--radius-full)' : 'var(--radius-1)' }}
    />
  );
}

export interface ListSkeletonColumn {
  /** Fixed width (e.g. "80px" or "20%"). Omit for flex: 1. */
  width?: string;
  minWidth?: string;
  /** The bar's width as a share of the column, 0 to 1. Default 0.6. */
  barWidth?: number;
  /** A glyph or avatar slot before the bar, such as a 32px avatar. */
  glyph?: {
    size: number;
    shape?: 'square' | 'circle';
    /** Space between the glyph and the bar. Default var(--space-3). */
    gap?: string;
  };
}

export type ListSkeletonVariant = 'sidebar-row' | 'two-line' | 'table-row' | 'card' | 'menu-row';

export interface ListSkeletonProps {
  variant: ListSkeletonVariant;
  /** Rows, groups or cards. Defaults per variant: 3, 4, 6, 3, 3. */
  count?: number;
  /** Row height in px; defaults to the real row's (sidebar 36, menu 32, table 60). */
  rowHeight?: number;
  /** Glyph slot before the text, in px (sidebar 14, inbox 32, menu 14). 0 for none. */
  glyphSize?: number;
  glyphShape?: 'square' | 'circle';
  /** table-row: one bar per column. */
  columns?: ListSkeletonColumn[];
  /** table-row: a leading checkbox square. */
  hasCheckbox?: boolean;
  checkboxWidth?: number;
  /** table-row: space reserved for the row-actions column. */
  hasRowActions?: boolean;
  actionsWidth?: number;
  /** table-row: horizontal padding of each data cell, to line up with the table's own cells. Default '0 var(--space-2)'. */
  cellPadding?: string;
  /** card: the real card's height and minimum width, in px. */
  cardHeight?: number;
  cardMinWidth?: number;
  /**
   * card: `auto-fill` (default) fits as many cards as `cardMinWidth` allows.
   * `card-grid` uses the shared `.card-grid` class, three columns that follow
   * the pane width; the caller wraps it in a `.card-grid-pane`.
   */
  cardLayout?: 'auto-fill' | 'card-grid';
  /** card: corner radius as a token, default var(--radius-3). */
  cardRadius?: string;
  /** card: inner padding as a token, default var(--space-4). */
  cardPadding?: string;
  /** card: space between cards as a token, default var(--space-3). The card grid sets its own. */
  cardGap?: string;
  /** What is loading, for screen readers ("Loading users…"). Defaults to "Loading…". */
  statusLabel?: string;
  /** Set when the surrounding region already announces the load. */
  announce?: boolean;
  /** The exit cross-fade from useDeferredLoading. */
  exiting?: boolean;
  /** Bars stay invisible (their space kept) this long, so a fast load shows none. */
  appearDelayMs?: number;
  style?: React.CSSProperties;
}

const SIDEBAR_WIDTHS = ['75%', '60%', '85%'];
const DEFAULT_COUNT: Record<ListSkeletonVariant, number> = {
  'sidebar-row': 3,
  'two-line': 4,
  'table-row': 6,
  card: 3,
  'menu-row': 3,
};
const DEFAULT_COLUMNS: ListSkeletonColumn[] = [
  { width: '20%', minWidth: '160px' },
  { barWidth: 0.8 },
  { width: '120px' },
  { width: '140px' },
];

/** False until the delay has passed, so a fast load never paints a bar. */
function useAppeared(delayMs: number): boolean {
  const [appeared, setAppeared] = useState(delayMs <= 0);
  useEffect(() => {
    if (delayMs <= 0) return;
    const timer = setTimeout(() => setAppeared(true), delayMs);
    return () => clearTimeout(timer);
  }, [delayMs]);
  return appeared;
}

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** Whether something around `ref` already carries a live status. */
function useAncestorStatus(ref: React.RefObject<HTMLElement | null>): boolean {
  const [has, setHas] = useState(false);
  useIsoLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    setHas(Boolean(parent?.closest('[role="status"], [aria-live="polite"], [aria-live="assertive"]')));
  }, [ref]);
  return has;
}

function Rows({ props }: { props: ListSkeletonProps }) {
  const {
    variant,
    count = DEFAULT_COUNT[variant],
    glyphShape = 'square',
    columns = DEFAULT_COLUMNS,
    hasCheckbox = true,
    checkboxWidth = 38,
    hasRowActions = true,
    actionsWidth = 80,
    cardHeight = 96,
    cardMinWidth = 240,
    cardLayout = 'auto-fill',
    cardRadius = 'var(--radius-3)',
    cardPadding = 'var(--space-4)',
    cardGap = 'var(--space-3)',
  } = props;
  const items = Array.from({ length: count });

  switch (variant) {
    case 'sidebar-row': {
      const rowHeight = props.rowHeight ?? 36;
      const glyphSize = props.glyphSize ?? 0;
      return (
        <>
          {items.map((_, i) => (
            <Flex
              key={i}
              align="center"
              gap="2"
              style={{ height: rowHeight, padding: '0 var(--space-3)', boxSizing: 'border-box' }}
            >
              {glyphSize > 0 && <SkeletonGlyph size={glyphSize} shape={glyphShape} />}
              <SkeletonBar width={SIDEBAR_WIDTHS[i % SIDEBAR_WIDTHS.length]} />
            </Flex>
          ))}
        </>
      );
    }
    case 'two-line': {
      const glyphSize = props.glyphSize ?? 0;
      return (
        <Flex direction="column" gap="5" style={{ padding: '0 var(--space-3)' }}>
          {items.map((_, i) => (
            <Flex key={i} align="start" gap="3">
              {glyphSize > 0 && <SkeletonGlyph size={glyphSize} shape={glyphShape} />}
              <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
                <SkeletonBar width="calc(var(--space-9) * 2 + var(--space-3))" />
                <SkeletonBar width="calc(var(--space-9) * 5 + var(--space-7))" />
              </Flex>
            </Flex>
          ))}
        </Flex>
      );
    }
    case 'menu-row': {
      const rowHeight = props.rowHeight ?? 32;
      const glyphSize = props.glyphSize ?? 14;
      return (
        <>
          {items.map((_, i) => (
            <Flex
              key={i}
              align="center"
              gap="2"
              style={{ height: rowHeight, padding: '0 var(--space-2)', boxSizing: 'border-box' }}
            >
              {glyphSize > 0 && <SkeletonGlyph size={glyphSize} shape={glyphShape} />}
              <SkeletonBar width={SIDEBAR_WIDTHS[i % SIDEBAR_WIDTHS.length]} />
            </Flex>
          ))}
        </>
      );
    }
    case 'card':
      return (
        <Box
          className={cardLayout === 'card-grid' ? 'card-grid' : undefined}
          style={
            cardLayout === 'card-grid'
              ? undefined
              : {
                  display: 'grid',
                  gridTemplateColumns: `repeat(auto-fill, minmax(${cardMinWidth}px, 1fr))`,
                  gap: cardGap,
                }
          }
        >
          {items.map((_, i) => (
            <Flex
              key={i}
              direction="column"
              gap="3"
              style={{
                height: cardHeight,
                padding: cardPadding,
                boxSizing: 'border-box',
                border: '1px solid var(--border-subtle)',
                borderRadius: cardRadius,
                backgroundColor: 'var(--surface-raised)',
              }}
            >
              <SkeletonGlyph size={32} />
              <Flex direction="column" gap="2">
                <SkeletonBar width="60%" />
                <SkeletonBar width="40%" />
              </Flex>
            </Flex>
          ))}
        </Box>
      );
    case 'table-row': {
      const rowHeight = props.rowHeight ?? 60;
      return (
        <>
          {items.map((_, rowIndex) => (
            <Flex
              key={rowIndex}
              align="center"
              style={{
                height: rowHeight,
                borderBottom: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--surface-raised)',
              }}
            >
              {hasCheckbox && (
                <Flex
                  align="center"
                  justify="center"
                  style={{ width: checkboxWidth, flexShrink: 0, padding: '0 var(--space-2)', boxSizing: 'border-box' }}
                >
                  <SkeletonGlyph size={16} />
                </Flex>
              )}
              {columns.map((col, i) => (
                <Flex
                  key={i}
                  align="center"
                  style={{
                    width: col.width,
                    minWidth: col.minWidth,
                    flex: col.width ? undefined : 1,
                    padding: props.cellPadding ?? '0 var(--space-2)',
                    gap: col.glyph ? col.glyph.gap ?? 'var(--space-3)' : undefined,
                  }}
                >
                  {col.glyph && <SkeletonGlyph size={col.glyph.size} shape={col.glyph.shape} />}
                  <SkeletonBar width={`${Math.round((col.barWidth ?? 0.6) * 100)}%`} />
                </Flex>
              ))}
              {hasRowActions && <Box style={{ width: actionsWidth, flexShrink: 0 }} />}
            </Flex>
          ))}
        </>
      );
    }
    default:
      return null;
  }
}

export function ListSkeleton(props: ListSkeletonProps) {
  const { statusLabel, announce = true, exiting = false, appearDelayMs = LOADING_SHOW_DELAY_MS, style } = props;
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const appeared = useAppeared(appearDelayMs);
  const ancestorAnnounces = useAncestorStatus(ref);
  const label = statusLabel ?? t('loading.default');

  return (
    <Box
      ref={ref}
      aria-busy={exiting ? undefined : true}
      data-list-skeleton={props.variant}
      style={{
        width: '100%',
        opacity: exiting ? 0 : 1,
        transition: 'opacity var(--motion-control) var(--motion-ease)',
        ...style,
      }}
    >
      {announce && !ancestorAnnounces && !exiting && (
        <VisuallyHidden>
          <span role="status">{label}</span>
        </VisuallyHidden>
      )}
      <Box aria-hidden="true" style={{ visibility: appeared ? 'visible' : 'hidden' }}>
        <Rows props={props} />
      </Box>
    </Box>
  );
}
