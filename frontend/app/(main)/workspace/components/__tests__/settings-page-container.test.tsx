import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import { pagePaddingX } from '@/app/components/ui/responsive';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

import { SettingsPageContainer } from '../settings-page-container';

afterEach(() => cleanup());

describe('SettingsPageContainer', () => {
  it('fills the content area with 100px desktop sides and no cap', () => {
    const { getByTestId } = render(
      <SettingsPageContainer>
        <span data-testid="child" />
      </SettingsPageContainer>,
    );
    const el = getByTestId('child').parentElement as HTMLElement;
    expect(el.style.paddingInline).toBe(pagePaddingX(100));
    expect(el.style.maxWidth).toBe('');
    expect(el.style.paddingTop).toBe('32px');
  });

  it('accepts a max width and clears the hamburger on mobile', () => {
    mobile.value = true;
    const { getByTestId } = render(
      <SettingsPageContainer maxWidth={1280}>
        <span data-testid="child" />
      </SettingsPageContainer>,
    );
    const el = getByTestId('child').parentElement as HTMLElement;
    expect(el.style.maxWidth).toBe('1280px');
    expect(el.style.paddingTop).toBe('max(56px, var(--space-8))');
    mobile.value = false;
  });
});
