'use client';

import React, { useLayoutEffect, useRef, useState } from 'react';
import { useThemeAppearance } from '@/app/components/theme-provider';

export interface SegmentedTabsItem<V extends string> {
  value: V;
  label: string;
  disabled?: boolean;
  /** Shown after the label, e.g. a count. */
  badge?: React.ReactNode;
}

export interface SegmentedTabsProps<V extends string> {
  value: V;
  onValueChange: (next: V) => void;
  items: SegmentedTabsItem<V>[];
  /** 'radio' for filters (radiogroup/radio), 'tabs' when it switches panels (tablist/tab). */
  kind?: 'radio' | 'tabs';
  /** '1' = 12px labels (compact panels), '2' = 14px labels (page headers). */
  size?: '1' | '2';
  /** Stretch to the container's width with equal segments (narrow screens). */
  fill?: boolean;
  'aria-label'?: string;
  style?: React.CSSProperties;
}

const HEIGHT = 32;
const INSET = 2;
const RADIUS = 4;

/**
 * Segmented control whose segments size to their labels (equal side padding), unlike Radix
 * SegmentedControl, which forces equal-width segments. The selected segment reserves its
 * medium-weight width (globals.css .segmented-tab-label) so nothing shifts on selection.
 * The selected pill is one indicator that slides between segments (.segmented-tab-indicator).
 */
export function SegmentedTabs<V extends string>({
  value,
  onValueChange,
  items,
  kind = 'radio',
  size = '2',
  fill = false,
  'aria-label': ariaLabel,
  style,
}: SegmentedTabsProps<V>) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const isTabs = kind === 'tabs';

  // Indicator geometry, measured from the selected segment. Until it is measured the selected
  // segment paints its own pill, so there is no flash; the first placement does not animate.
  const rootRef = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState<{ left: number; width: number; animate: boolean } | null>(null);
  const selectedIndex = items.findIndex((i) => i.value === value);
  // One tab stop: the selected segment, or the first enabled one when nothing selectable is selected
  const tabStop =
    selectedIndex >= 0 && !items[selectedIndex]!.disabled
      ? selectedIndex
      : items.findIndex((i) => !i.disabled);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const place = () => {
      const btn = root.querySelectorAll<HTMLButtonElement>('button')[selectedIndex];
      if (!btn) return setPill(null);
      setPill((prev) => {
        // Sub-pixel rects (offsetLeft/offsetWidth round), divided by any ancestor scale so a
        // container mid scale-in animation doesn't skew the measurement
        const b = btn.getBoundingClientRect();
        const r = root.getBoundingClientRect();
        const scale = root.offsetWidth ? r.width / root.offsetWidth : 1;
        const next = { left: (b.left - r.left) / scale, width: b.width / scale };
        if (prev && prev.left === next.left && prev.width === next.width) return prev;
        return { ...next, animate: prev !== null };
      });
    };
    place();
    if (typeof ResizeObserver === 'undefined') return;
    // Labels change width (counts load, fonts arrive), so follow the segment's size
    const ro = new ResizeObserver(place);
    root.querySelectorAll('button').forEach((b) => ro.observe(b));
    return () => ro.disconnect();
  }, [selectedIndex, items.length]);

  const enabled = items.filter((i) => !i.disabled);
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step || enabled.length === 0) return;
    e.preventDefault();
    const i = enabled.findIndex((it) => it.value === value);
    const next = enabled[(i + step + enabled.length) % enabled.length]!;
    onValueChange(next.value);
    e.currentTarget
      .querySelectorAll<HTMLButtonElement>('button')
      [items.indexOf(next)]?.focus();
  };

  const inactiveColor = isDark ? 'rgba(252, 253, 255, 0.937255)' : 'rgba(0, 5, 9, 0.89)';
  const selectedBorder = isDark
    ? '1px solid rgba(211, 237, 248, 0.113725)'
    : '1px solid rgba(0, 0, 45, 0.09)';
  const selectedBg = isDark ? '#111113' : 'var(--color-panel-solid, #ffffff)';

  return (
    <div
      ref={rootRef}
      role={isTabs ? 'tablist' : 'radiogroup'}
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      style={{
        minWidth: 0,
        maxWidth: '100%',
        height: HEIGHT,
        borderRadius: RADIUS,
        display: fill ? 'flex' : 'inline-flex',
        width: fill ? '100%' : undefined,
        flexDirection: 'row',
        alignItems: 'center',
        gap: INSET,
        padding: INSET,
        boxSizing: 'border-box',
        flexShrink: 0,
        position: 'relative',
        background: isDark
          ? 'linear-gradient(90deg, rgba(221, 234, 248, 0.0784314) 0%, rgba(221, 234, 248, 0.0784314) 100%), rgba(0, 0, 0, 0.25)'
          : 'linear-gradient(90deg, rgba(0, 0, 51, 0.059) 0%, rgba(0, 0, 51, 0.059) 100%), linear-gradient(90deg, rgba(255, 255, 255, 0.9) 0%, rgba(255, 255, 255, 0.9) 100%)',
        ...style,
      }}
    >
      {pill && selectedIndex >= 0 && (
        <span
          aria-hidden
          className={pill.animate ? 'segmented-tab-indicator is-animated' : 'segmented-tab-indicator'}
          style={{
            position: 'absolute',
            top: INSET,
            left: 0,
            width: pill.width,
            height: HEIGHT - 2 * INSET,
            transform: `translateX(${pill.left}px)`,
            boxSizing: 'border-box',
            borderRadius: RADIUS - 1,
            border: selectedBorder,
            background: selectedBg,
            pointerEvents: 'none',
          }}
        />
      )}
      {items.map((item, index) => {
        const selected = item.value === value;
        // Once the sliding indicator is placed, it paints the selected pill instead of the segment
        const paintsOwnPill = selected && !pill;
        return (
          <button
            key={item.value}
            type="button"
            role={isTabs ? 'tab' : 'radio'}
            {...(isTabs ? { 'aria-selected': selected } : { 'aria-checked': selected })}
            data-state={selected ? 'on' : 'off'}
            aria-disabled={item.disabled}
            disabled={item.disabled}
            // Roving focus: only the selected segment is a tab stop; arrows move between them
            tabIndex={index === tabStop ? 0 : -1}
            onClick={() => {
              if (!item.disabled) onValueChange(item.value);
            }}
            className="segmented-tab"
            style={{
              boxSizing: 'border-box',
              flex: fill ? '1 1 0' : '0 1 auto',
              gap: 6,
              minWidth: 0,
              height: HEIGHT - 2 * INSET,
              padding: '0 12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: RADIUS - 1,
              position: 'relative',
              border: paintsOwnPill ? selectedBorder : '1px solid transparent',
              // Unselected background comes from .segmented-tab (hover state) in globals.css
              background: paintsOwnPill ? selectedBg : undefined,
              color: item.disabled ? 'var(--gray-7)' : inactiveColor,
              fontSize: size === '1' ? 12 : 14,
              lineHeight: size === '1' ? '16px' : '20px',
              letterSpacing: size === '1' ? '0.04px' : undefined,
              fontWeight: selected ? 500 : 400,
              fontFamily: 'inherit',
              cursor: item.disabled ? 'not-allowed' : 'pointer',
              opacity: item.disabled ? 0.5 : 1,
            }}
          >
            <span
              className="segmented-tab-label"
              data-label={item.label}
              style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {item.label}
            </span>
            {item.badge}
          </button>
        );
      })}
    </div>
  );
}
