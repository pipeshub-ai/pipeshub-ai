import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import { pagePaddingX } from '@/app/components/ui/responsive';

// Phone ≤768, drawer layout ≤1023: a tablet is the drawer layout without being a phone.
const width = vi.hoisted(() => ({ phone: false, drawer: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => width.phone, useIsBelowDesktop: () => width.drawer }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => true }));

import { SettingsPageContainer } from '../settings-page-container';
import { EntityPageHeader } from '../entity-page-header';

const at = (w: 'phone' | 'tablet' | 'desktop') => {
  width.phone = w === 'phone';
  width.drawer = w !== 'desktop';
};

afterEach(() => {
  cleanup();
  at('desktop');
});

const container = (maxWidth?: number) => {
  const { getByTestId } = render(
    <SettingsPageContainer maxWidth={maxWidth}>
      <span data-testid="child" />
    </SettingsPageContainer>,
  );
  return getByTestId('child').parentElement as HTMLElement;
};

const header = (paddingTop?: string) =>
  render(
    <EntityPageHeader
      title="Users"
      subtitle="Everyone"
      searchValue=""
      onSearchChange={() => {}}
      ctaLabel="Invite"
      ctaIcon="add"
      onCtaClick={() => {}}
      paddingTop={paddingTop}
    />,
  ).getByText('Users').closest('div[style*="padding-top"]') as HTMLElement;

describe('SettingsPageContainer', () => {
  it('fills the content area with 100px desktop sides and no cap', () => {
    const el = container();
    expect(el.style.paddingInline).toBe(pagePaddingX(100));
    expect(el.style.maxWidth).toBe('');
    expect(el.style.paddingTop).toBe('32px');
  });

  it('accepts a max width', () => {
    expect(container(1280).style.maxWidth).toBe('1280px');
  });

  // Below 1024px the Open sidebar row (40px) sits above the page; the title must not drop by it.
  it.each(['phone', 'tablet'] as const)('on a %s takes the narrow padding under the Open sidebar row', (w) => {
    at(w);
    expect(container().style.paddingTop).toBe('var(--space-4)');
  });
});

describe('EntityPageHeader', () => {
  it('keeps the settings pages\' 32px above the title on a desktop', () => {
    expect(header().style.paddingTop).toBe('32px');
  });

  it.each(['phone', 'tablet'] as const)('on a %s takes the narrow padding under the Open sidebar row', (w) => {
    at(w);
    expect(header().style.paddingTop).toBe('var(--space-4)');
  });

  it('still takes a padding it is given, at any width', () => {
    at('tablet');
    expect(header('var(--space-2)').style.paddingTop).toBe('var(--space-2)');
  });
});
