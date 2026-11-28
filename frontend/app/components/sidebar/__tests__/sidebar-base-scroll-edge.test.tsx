/**
 * The opt-in scroll edge: a 1px line at the top of the sidebar's scroll area while it is scrolled,
 * hidden at the top, and absent from every sidebar that does not ask for it. And the pinned block:
 * rows under the header that stay out of the scroll area, with the edge below them.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { SidebarBase } from '../sidebar-base';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

function sidebar({
  isMobile = false,
  scrollEdgeDivider,
  pinned,
}: {
  isMobile?: boolean;
  scrollEdgeDivider?: boolean;
  pinned?: React.ReactNode;
}) {
  return (
    <Theme>
      <SidebarBase
        header={<div>Header</div>}
        pinned={pinned}
        isMobile={isMobile}
        mobileOpen={isMobile}
        onMobileClose={vi.fn()}
        scrollEdgeDivider={scrollEdgeDivider}
      >
        <span>Nav items</span>
      </SidebarBase>
    </Theme>
  );
}

const divider = () => document.querySelector<HTMLElement>('[data-sidebar-scroll-edge]');
const scrollArea = () => screen.getByText('Nav items').parentElement as HTMLElement;

function scrollTo(top: number) {
  const area = scrollArea();
  area.scrollTop = top;
  fireEvent.scroll(area);
}

describe('SidebarBase scroll edge', () => {
  it.each([false, true])('is hidden at the top, shows once scrolled, hides again back at the top (drawer: %s)', (isMobile) => {
    render(sidebar({ isMobile, scrollEdgeDivider: true }));

    expect(divider()).not.toBeNull();
    expect(divider()!.style.visibility).toBe('hidden');
    expect(divider()!.style.backgroundColor).toBe('var(--border-subtle)');
    expect(divider()!.style.position).toBe('relative');

    scrollTo(40);
    expect(divider()!.style.visibility).toBe('visible');
    expect(divider()!.dataset.visible).toBe('true');

    scrollTo(0);
    expect(divider()!.style.visibility).toBe('hidden');
  });

  it.each([false, true])('sits right before the scroll area and takes no height (drawer: %s)', (isMobile) => {
    render(sidebar({ isMobile, scrollEdgeDivider: true }));
    expect(divider()!.nextElementSibling).toBe(scrollArea());
    expect(divider()!.style.height).toBe('1px');
    expect(divider()!.style.marginBottom).toBe('-1px');
  });

  it.each([false, true])('a pinned block stays out of the scroll area, with the edge below it (drawer: %s)', (isMobile) => {
    render(sidebar({ isMobile, scrollEdgeDivider: true, pinned: <span>Pinned rows</span> }));
    const pinnedBlock = screen.getByText('Pinned rows').parentElement as HTMLElement;
    expect(scrollArea().contains(pinnedBlock)).toBe(false);
    expect(pinnedBlock.nextElementSibling).toBe(divider());
    expect(divider()!.nextElementSibling).toBe(scrollArea());
    // The gap below the rows is outside the scroll area, so the lists are cut where they start.
    expect(pinnedBlock.style.padding).toBe('var(--space-4) var(--space-2) var(--space-3)');
    expect(scrollArea().style.padding).toBe('0 var(--space-2) var(--space-4)');
    // On a short rail the lists keep three rows and the whole column scrolls.
    expect(scrollArea().style.minHeight).toBe('96px');
    expect(scrollArea().parentElement!.style.overflowY).toBe('auto');
    // Its rows' visually hidden controls are held by the scroll area, not stretched across the column.
    expect(scrollArea().style.position).toBe('relative');

    scrollTo(40);
    expect(divider()!.style.visibility).toBe('visible');
  });

  it.each([false, true])('without a pinned block the layout is as before (drawer: %s)', (isMobile) => {
    render(sidebar({ isMobile }));
    expect(scrollArea().style.padding).toBe('var(--space-4) var(--space-2)');
    expect(scrollArea().style.position).toBe('');
    expect(scrollArea().parentElement!.style.overflowY).toBe(isMobile ? 'hidden' : '');
  });

  it.each([false, true])('a sidebar without the prop renders no divider (drawer: %s)', (isMobile) => {
    render(sidebar({ isMobile }));
    expect(divider()).toBeNull();
    scrollTo(40);
    expect(divider()).toBeNull();
  });
});
