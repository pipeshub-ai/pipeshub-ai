import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen, act } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import '@/lib/__tests__/test-i18n';
import { ListSkeleton, type ListSkeletonVariant } from '../list-skeleton';
import { TableSkeleton } from '@/app/components/data-display/table-skeleton';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const bars = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>('.skeleton-shimmer')];

describe('ListSkeleton', () => {
  it.each(['sidebar-row', 'two-line', 'table-row', 'card', 'menu-row'] as ListSkeletonVariant[])(
    '%s: bars on the fill-active role, smallest radius, shimmer class, hidden from assistive technology',
    (variant) => {
      const { container } = render(<ListSkeleton variant={variant} appearDelayMs={0} />);
      const all = bars(container);
      expect(all.length).toBeGreaterThan(0);
      for (const bar of all) {
        expect(bar.style.backgroundColor).toBe('var(--fill-active)');
        expect(bar.style.borderRadius === 'var(--radius-1)' || bar.style.borderRadius === 'var(--radius-full)').toBe(true);
        expect(bar.closest('[aria-hidden="true"]')).toBeTruthy();
        // In phase: no per-bar delay.
        expect(bar.style.animationDelay).toBe('');
      }
    },
  );

  it('uses 10px bars for list text', () => {
    const { container } = render(<ListSkeleton variant="sidebar-row" appearDelayMs={0} />);
    expect(bars(container)[0].style.height).toBe('calc(var(--space-2) + var(--space-1) / 2)');
  });

  it('is busy and announces one polite status with a name', () => {
    render(<ListSkeleton variant="two-line" statusLabel="Loading chats…" />);
    const status = screen.getByRole('status');
    expect(status.textContent).toBe('Loading chats…');
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(status.closest('[aria-busy="true"]')).toBeTruthy();
  });

  it('adds no second status inside a region that already announces the load', () => {
    render(
      <div role="status" aria-label="Loading records">
        <ListSkeleton variant="table-row" />
      </div>,
    );
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });

  it('keeps its space but paints no bar during the show delay', () => {
    vi.useFakeTimers();
    const { container } = render(<ListSkeleton variant="menu-row" />);
    const layer = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(layer.style.visibility).toBe('hidden');
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(layer.style.visibility).toBe('visible');
  });

  it('fades out on the control motion role when exiting, and stops announcing', () => {
    const { container } = render(<ListSkeleton variant="menu-row" exiting />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.opacity).toBe('0');
    expect(root.style.transition).toContain('var(--motion-control)');
    expect(root.getAttribute('aria-busy')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('names no raw gray family in its source', () => {
    for (const file of ['../list-skeleton.tsx', '../../data-display/table-skeleton.tsx']) {
      const src = readFileSync(path.join(__dirname, file), 'utf8');
      expect(src, file).not.toMatch(/--(slate|olive|neutral)-|shimmer-pulse|animation: 'pulse/);
    }
  });
});

describe('TableSkeleton', () => {
  it('keeps its consumers\' row pitch and columns', () => {
    const { container } = render(
      <TableSkeleton rows={3} rowHeight={56} columns={[{ barWidth: 0.5 }, { width: '140px' }]} hasCheckbox />,
    );
    const rows = container.querySelectorAll<HTMLElement>('[data-list-skeleton] [aria-hidden="true"] > div');
    expect(rows).toHaveLength(3);
    expect(rows[0].style.height).toBe('56px');
    expect(rows[0].style.borderBottom).toBe('1px solid var(--border-subtle)');
    expect(rows[0].style.backgroundColor).toBe('var(--surface-raised)');
    // checkbox square + two column bars
    expect(rows[0].querySelectorAll('.skeleton-shimmer')).toHaveLength(3);
  });

  it('announces what is loading', () => {
    render(<TableSkeleton />);
    expect(screen.getByRole('status').textContent).toBe('Loading items…');
  });
});

describe('ListSkeleton card options', () => {
  const grid = (container: HTMLElement) =>
    container.querySelector<HTMLElement>('[data-list-skeleton] [aria-hidden="true"] > div')!;
  const cards = (container: HTMLElement) => [...grid(container).children] as HTMLElement[];

  it('keeps today\'s card grid and card box by default', () => {
    const { container } = render(<ListSkeleton variant="card" appearDelayMs={0} />);
    const g = grid(container);
    expect(g.className).not.toContain('card-grid');
    expect(g.style.display).toBe('grid');
    expect(g.style.gridTemplateColumns).toBe('repeat(auto-fill, minmax(240px, 1fr))');
    expect(g.style.gap).toBe('var(--space-3)');
    expect(cards(container)[0].style.borderRadius).toBe('var(--radius-3)');
    expect(cards(container)[0].style.padding).toBe('var(--space-4)');
  });

  it('uses the shared card grid class for the card-grid layout', () => {
    const { container } = render(<ListSkeleton variant="card" cardLayout="card-grid" cardGap="var(--space-5)" appearDelayMs={0} />);
    const g = grid(container);
    expect(g.classList.contains('card-grid')).toBe(true);
    expect(g.style.gridTemplateColumns).toBe('');
    expect(g.style.gap).toBe('');
    // No pane of its own: the caller provides the card-grid pane.
    expect(container.querySelector('.card-grid-pane')).toBeNull();
  });

  it('applies the radius, padding and gap props', () => {
    const { container } = render(
      <ListSkeleton
        variant="card"
        cardRadius="var(--radius-1)"
        cardPadding="var(--space-3)"
        cardGap="var(--space-4)"
        appearDelayMs={0}
      />,
    );
    expect(grid(container).style.gap).toBe('var(--space-4)');
    for (const card of cards(container)) {
      expect(card.style.borderRadius).toBe('var(--radius-1)');
      expect(card.style.padding).toBe('var(--space-3)');
    }
  });
});

describe('ListSkeleton column glyph', () => {
  const firstRowCells = (container: HTMLElement) =>
    [...container.querySelector<HTMLElement>('[data-list-skeleton] [aria-hidden="true"] > div')!.children] as HTMLElement[];

  it('puts the glyph before the bar with a 12px default gap', () => {
    const { container } = render(
      <ListSkeleton
        variant="table-row"
        count={1}
        hasCheckbox={false}
        hasRowActions={false}
        columns={[{ glyph: { size: 32 } }, { width: '120px' }]}
        appearDelayMs={0}
      />,
    );
    const [withGlyph, plain] = firstRowCells(container);
    const parts = withGlyph.querySelectorAll<HTMLElement>('.skeleton-shimmer');
    expect(parts).toHaveLength(2);
    expect(parts[0].style.width).toBe('32px');
    expect(parts[0].style.height).toBe('32px');
    expect(parts[0].style.borderRadius).toBe('var(--radius-1)');
    expect(parts[1].style.width).toBe('60%');
    expect(withGlyph.style.gap).toBe('var(--space-3)');
    expect(plain.style.gap).toBe('');
    expect(plain.querySelectorAll('.skeleton-shimmer')).toHaveLength(1);
  });

  it('draws a circle glyph on the full radius and takes a custom gap', () => {
    const { container } = render(
      <ListSkeleton
        variant="table-row"
        count={1}
        hasCheckbox={false}
        hasRowActions={false}
        columns={[{ glyph: { size: 28, shape: 'circle', gap: 'var(--space-2)' } }]}
        appearDelayMs={0}
      />,
    );
    const [cell] = firstRowCells(container);
    expect(cell.querySelector<HTMLElement>('.skeleton-shimmer')!.style.borderRadius).toBe('var(--radius-full)');
    expect(cell.style.gap).toBe('var(--space-2)');
  });
});
