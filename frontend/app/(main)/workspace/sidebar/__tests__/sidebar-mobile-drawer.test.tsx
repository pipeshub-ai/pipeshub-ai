import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const mobile = vi.hoisted(() => ({ value: true }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

let pathname = '/workspace/general';
vi.mock('next/navigation', () => ({ usePathname: () => pathname }));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: unknown) => unknown) => selector({ profile: { isAdmin: false } }),
  selectIsAdmin: () => false,
}));
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: () => true,
  selectMcpEnabled: () => true,
  selectActionsEnabled: () => true,
  selectSkillsEnabled: () => true,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import WorkspaceSidebar from '../index';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';

const ui = () => (
  <Theme>
    <WorkspaceSidebar />
  </Theme>
);

describe('WorkspaceSidebar on a phone', () => {
  beforeEach(() => {
    mobile.value = true;
    pathname = '/workspace/general';
    useMobileSidebarStore.setState({ isOpen: false });
  });
  afterEach(cleanup);

  it('renders nothing until the drawer is opened, so the page gets the full width', () => {
    render(ui());
    expect(screen.queryByText('workspace.sidebar.nav.profile')).toBeNull();
    act(() => useMobileSidebarStore.getState().open());
    expect(screen.getByText('workspace.sidebar.nav.profile')).toBeTruthy();
  });

  it('closes the drawer when the route changes', () => {
    const view = render(ui());
    act(() => useMobileSidebarStore.getState().open());
    pathname = '/workspace/profile';
    view.rerender(ui());
    expect(useMobileSidebarStore.getState().isOpen).toBe(false);
  });

  it('stays inline on desktop', () => {
    mobile.value = false;
    render(ui());
    expect(screen.getByText('workspace.sidebar.nav.profile')).toBeTruthy();
  });
});
