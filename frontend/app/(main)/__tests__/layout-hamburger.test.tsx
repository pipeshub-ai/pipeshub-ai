import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { MobileMenuButton } from '../components/mobile-menu-button';

let pathname = '/agents/new/';
vi.mock('next/navigation', () => ({ usePathname: () => pathname }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const label = en.sidebar.open;
const ui = () => (
  <Theme>
    <MobileMenuButton />
  </Theme>
);

beforeEach(() => {
  pathname = '/agents/new/';
  useMobileSidebarStore.setState({ isOpen: false, registeredCount: 0 });
});
afterEach(() => cleanup());

describe('MobileMenuButton', () => {
  it('is hidden on a route with no mobile sidebar and shows once one registers', () => {
    render(ui());
    expect(screen.queryByRole('button', { name: label })).toBeNull();

    act(() => {
      useMobileSidebarStore.getState().register();
    });
    expect(screen.getByRole('button', { name: label })).toBeTruthy();
  });

  it('closes the drawer when the pathname changes', () => {
    const { rerender } = render(ui());
    act(() => useMobileSidebarStore.getState().open());
    expect(useMobileSidebarStore.getState().isOpen).toBe(true);

    pathname = '/chat/';
    rerender(ui());
    expect(useMobileSidebarStore.getState().isOpen).toBe(false);
  });
});
