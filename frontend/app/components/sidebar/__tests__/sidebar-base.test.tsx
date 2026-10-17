import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { SidebarBase } from '../sidebar-base';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

function sidebar(isMobile: boolean, mobileOpen: boolean) {
  return (
    <Theme>
      <SidebarBase isMobile={isMobile} mobileOpen={mobileOpen} onMobileClose={vi.fn()}>
        <span>Nav items</span>
      </SidebarBase>
    </Theme>
  );
}

describe('SidebarBase', () => {
  // useIsMobile reports desktop on the first render and the real width after mount,
  // so on a phone every page renders this sidebar as desktop first, then as mobile.
  it.each([false, true])('switches from desktop to mobile without crashing (drawer open: %s)', (mobileOpen) => {
    const { rerender } = render(sidebar(false, false));
    expect(screen.getByText('Nav items')).toBeTruthy();

    rerender(sidebar(true, mobileOpen));

    expect(Boolean(screen.queryByText('Nav items'))).toBe(mobileOpen);
  });

  it('switches back from mobile to desktop', () => {
    const { rerender } = render(sidebar(true, false));
    rerender(sidebar(false, false));
    expect(screen.getByText('Nav items')).toBeTruthy();
  });
});
