/**
 * WSMOBILE: at phone width the workspace sidebar is a drawer behind the app's menu button.
 * WM-1 closed by default; WM-2 opens with focus inside and closes back to the button;
 * WM-3 Esc closes; WM-4 a route change closes; WM-5 desktop keeps the inline sidebar.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';

let pathname = '/workspace/general';
vi.mock('next/navigation', () => ({ usePathname: () => pathname }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/durable-default-notice', () => ({ DurableDefaultNotice: () => null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: unknown) => unknown) => selector({}),
  selectIsAdmin: () => true,
  selectRefreshedIsAdmin: () => true,
}));
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: unknown) => unknown) => selector({}),
  selectMcpEnabled: () => false,
  selectActionsEnabled: () => false,
  selectSkillsEnabled: () => false,
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => true,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => false,
}));

import WorkspaceLayout from '../workspaceLayout';
import { MobileMenuButton } from '../../components/mobile-menu-button';

function mockWidth(mobile: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: mobile,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

const shell = () => (
  <Theme>
    <MobileMenuButton />
    <WorkspaceLayout>
      <p>Page content</p>
    </WorkspaceLayout>
  </Theme>
);

const menu = () => screen.getByRole('button', { name: 'Open sidebar' });
const drawer = () => document.getElementById('app-mobile-nav');
const general = () => screen.queryByRole('link', { name: 'General' });

beforeEach(() => {
  pathname = '/workspace/general';
  useMobileSidebarStore.setState({ isOpen: false });
  mockWidth(true);
});

afterEach(() => cleanup());

describe('workspace layout at phone width', () => {
  it('WM-1: the sidebar is closed by default and the content is shown', () => {
    render(shell());
    expect(general()).toBeNull();
    expect(screen.getByText('Page content')).toBeTruthy();
    expect(menu().getAttribute('aria-expanded')).toBe('false');
    expect(menu().getAttribute('aria-controls')).toBe('app-mobile-nav');
  });

  it('WM-2: the menu button opens the nav with focus inside; close returns focus to the button', () => {
    render(shell());
    menu().focus();
    fireEvent.click(menu());
    expect(menu().getAttribute('aria-expanded')).toBe('true');
    expect(general()).toBeTruthy();
    expect(drawer()?.contains(document.activeElement)).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Close sidebar' }));
    expect(drawer()).toBeNull();
    expect(document.activeElement).toBe(menu());
  });

  it('WM-3: Esc closes the nav and focus returns to the button', () => {
    render(shell());
    menu().focus();
    fireEvent.click(menu());
    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' });
    expect(drawer()).toBeNull();
    expect(menu().getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(menu());
  });

  it('WM-4: navigating to another page closes the nav', () => {
    const { rerender } = render(shell());
    fireEvent.click(menu());
    expect(drawer()).toBeTruthy();
    pathname = '/workspace/prompts';
    act(() => rerender(shell()));
    expect(drawer()).toBeNull();
  });

  it('WM-5: on desktop the sidebar stays inline beside the content', () => {
    mockWidth(false);
    render(shell());
    expect(general()).toBeTruthy();
    expect(drawer()).toBeNull();
  });
});
