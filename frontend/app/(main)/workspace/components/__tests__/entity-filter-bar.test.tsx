import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const mobile = vi.hoisted(() => ({ value: true }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsBelowDesktop: () => mobile.value }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { EntityFilterBar } from '../entity-filter-bar';

afterEach(() => cleanup());

const filters = [
  { key: 'a', label: 'Role', icon: 'badge' },
  { key: 'b', label: 'Status', icon: 'info' },
];

describe('EntityFilterBar', () => {
  it('scrolls horizontally below desktop and keeps chips from shrinking', () => {
    mobile.value = true;
    const { container } = render(<Theme><EntityFilterBar filters={filters} /></Theme>);
    const bar = container.querySelector('.entity-filter-bar-mobile') as HTMLElement;
    expect(bar.classList.contains('no-scrollbar')).toBe(true);
    expect(bar.style.overflowX).toBe('auto');
    expect(bar.style.flexWrap).toBe('nowrap');
    expect(container.querySelector('style')).toBeNull(); // the shrink rule lives in globals.css
  });

  it('is unchanged on desktop', () => {
    mobile.value = false;
    const { container } = render(<Theme><EntityFilterBar filters={filters} /></Theme>);
    expect(container.querySelector('.entity-filter-bar-mobile')).toBeNull();
    expect(container.querySelector('style')).toBeNull();
  });
});
