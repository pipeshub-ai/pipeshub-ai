/**
 * Show sidebar and the Ctrl+\ (⌘\ on macOS) shortcut: one listener in the app shell, never in
 * the drawer layout, in workspace settings or under a modal, and focus never lost to the page.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const state = vi.hoisted(() => ({ drawer: false, path: '/chat/' }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => state.drawer, useIsBelowDesktop: () => state.drawer }));
vi.mock('next/navigation', () => ({ usePathname: () => state.path }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { SidebarExpandButton } from '../sidebar-expand-button';

const collapsed = () => useSidebarWidthStore.getState().isNavCollapsed;
const press = (init: KeyboardEventInit = {}) =>
  act(() => {
    fireEvent.keyDown(window, { key: '\\', code: 'Backslash', ctrlKey: true, ...init });
  });

function Shell() {
  return (
    <Theme>
      <div data-ph-sidebar-slot="">
        <button type="button" data-sidebar-toggle="hide" aria-label="Hide sidebar" />
      </div>
      <SidebarExpandButton placement="shell" />
    </Theme>
  );
}

beforeEach(() => {
  installBrowserShims();
  state.drawer = false;
  state.path = '/chat/';
  useSidebarWidthStore.getState().setNavCollapsed(false);
  // jsdom lays nothing out; give every element a box so "visible" checks pass.
  vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList);
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => window.setTimeout(() => cb(0), 0));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSidebarWidthStore.getState().setNavCollapsed(false);
});

describe('the sidebar shortcut', () => {
  it('hides, then shows the rail, one toggle per press', () => {
    render(<Shell />);
    press();
    expect(collapsed()).toBe(true);
    press();
    expect(collapsed()).toBe(false);
  });

  it('does nothing in the drawer layout', () => {
    state.drawer = true;
    render(<Shell />);
    press();
    expect(collapsed()).toBe(false);
  });

  it('does nothing in workspace settings', () => {
    state.path = '/workspace/profile/';
    render(<Shell />);
    press();
    expect(collapsed()).toBe(false);
  });

  it('does nothing while a modal dialog is open', () => {
    render(<Shell />);
    const overlay = document.createElement('div');
    overlay.className = 'rt-BaseDialogOverlay';
    document.body.appendChild(overlay);
    press();
    expect(collapsed()).toBe(false);
    overlay.remove();
  });

  it('moves focus to Show sidebar when it hides the rail that had focus', async () => {
    render(<Shell />);
    act(() => screen.getByRole('button', { name: 'Hide sidebar' }).focus());
    press();
    await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Show sidebar'));
  });
});

describe('Show sidebar', () => {
  it('is a 24px ghost icon button named Show sidebar, at the rail column', () => {
    useSidebarWidthStore.getState().setNavCollapsed(true);
    render(<Shell />);
    const show = screen.getByRole('button', { name: 'Show sidebar' });
    expect(show.classList.contains('rt-IconButton')).toBe(true);
    expect(show.classList.contains('touch-hit')).toBe(true);
    expect(show.style.width).toBe('var(--space-5)');
    expect((show.parentElement as HTMLElement).style.left).toBe('calc(var(--space-4) + var(--space-1))');
  });

  it('shows the rail and hands focus to Hide sidebar', async () => {
    useSidebarWidthStore.getState().setNavCollapsed(true);
    render(<Shell />);
    fireEvent.click(screen.getByRole('button', { name: 'Show sidebar' }));
    expect(collapsed()).toBe(false);
    await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Hide sidebar'));
  });
});
