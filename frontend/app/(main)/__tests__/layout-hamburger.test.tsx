import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { MobileMenuButton, isChatRoute } from '../components/mobile-menu-button';

let pathname = '/agents/new/';
vi.mock('next/navigation', () => ({ usePathname: () => pathname }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true, useIsBelowDesktop: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name, size, color }: { name: string; size?: number; color?: string }) => (
    <i data-icon={name} data-size={size} data-color={color} />
  ),
}));

const label = en.sidebar.open;
const ui = () => (
  <Theme>
    <MobileMenuButton />
  </Theme>
);

beforeEach(() => {
  pathname = '/agents/new/';
  useMobileSidebarStore.setState({ isOpen: false, registeredCount: 0 });
  useSidebarWidthStore.setState({ pageExpandControls: 0 });
  useNotificationStore.setState({ isPanelOpen: false });
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

  it.each(['/chat/', '/chat'])('on a chat page (%s) is a clear 40px ghost button, 8px in, with a touch-sized hit area, no ground and no shadow', (path) => {
    pathname = path;
    useMobileSidebarStore.setState({ registeredCount: 1 });
    render(ui());
    const button = screen.getByRole('button', { name: label });
    expect(button.classList.contains('touch-hit')).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    const ground = button.parentElement as HTMLElement;
    expect(ground.style.top).toBe('var(--space-2)');
    expect(ground.style.left).toBe('var(--space-2)');
    expect(ground.style.width).toBe('var(--space-7)');
    expect(ground.style.backgroundColor).toBe('');
    expect(ground.style.background).toBe('');
    expect(ground.getAttribute('style')).not.toContain('--surface-raised');
    expect(button.getAttribute('style') ?? '').not.toContain('--surface-raised');
    expect(button.style.backgroundColor).toBe('');
    expect(ground.style.borderRadius).toBe('var(--radius-3)');
    expect(button.classList.contains('rt-variant-ghost')).toBe(true);
    expect(ground.style.boxShadow).toBe('');
    expect(button.style.boxShadow).toBe('');
  });

  it.each(['/workspace/profile/', '/workspace/users/', '/knowledge-base/', '/agents/new/'])(
    'off chat (%s), with no header control on the page, it takes a row of its own in the flow: 40px tall, 12px in, a 24px ghost box, no ground',
    (path) => {
      pathname = path;
      useMobileSidebarStore.setState({ registeredCount: 1 });
      render(ui());
      const button = screen.getByRole('button', { name: label });
      const row = button.parentElement as HTMLElement;
      expect(row.style.position).toBe('');
      expect(row.style.height).toBe('var(--space-7)');
      expect(row.style.padding).toContain('var(--space-3)');
      expect(row.style.backgroundColor).toBe('');
      expect(button.style.width).toBe('var(--space-5)');
      expect(button.style.height).toBe('var(--space-5)');
      expect(button.style.backgroundColor).toBe('');
      expect(button.classList.contains('rt-variant-ghost')).toBe(true);
      expect(button.classList.contains('rt-r-size-1')).toBe(true);
      expect(button.classList.contains('touch-hit')).toBe(true);
      expect(button.getAttribute('aria-haspopup')).toBe('dialog');
      expect(button.getAttribute('aria-expanded')).toBe('false');
    },
  );

  it('gives way to a page header that holds the control: one Open sidebar, in the header, and it opens the drawer', () => {
    pathname = '/knowledge-base/';
    useMobileSidebarStore.setState({ registeredCount: 1 });
    render(
      <Theme>
        <MobileMenuButton />
        <header data-testid="page-header">
          <SidebarExpandButton placement="inline" />
        </header>
      </Theme>,
    );
    expect(screen.getAllByRole('button', { name: label })).toHaveLength(1);
    const button = within(screen.getByTestId('page-header')).getByRole('button', { name: label });
    fireEvent.click(button);
    expect(useMobileSidebarStore.getState().isOpen).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('true');
  });

  it('in the drawer layout only an inline SidebarExpandButton draws, and only while a drawer is mounted', () => {
    const { rerender } = render(
      <Theme>
        <SidebarExpandButton placement="inline" />
        <SidebarExpandButton />
        <SidebarExpandButton placement="shell" />
      </Theme>,
    );
    expect(screen.queryByRole('button', { name: label })).toBeNull();
    act(() => {
      useMobileSidebarStore.getState().register();
    });
    rerender(
      <Theme>
        <SidebarExpandButton placement="inline" />
        <SidebarExpandButton />
        <SidebarExpandButton placement="shell" />
      </Theme>,
    );
    expect(screen.getAllByRole('button', { name: label })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: en.sidebar.show })).toBeNull();
  });

  it("on a chat page keeps its own band even though the chat's corner control is mounted", () => {
    pathname = '/chat/';
    useMobileSidebarStore.setState({ registeredCount: 1 });
    render(
      <Theme>
        <MobileMenuButton />
        <SidebarExpandButton />
      </Theme>,
    );
    const button = screen.getByRole('button', { name: label });
    expect((button.parentElement as HTMLElement).style.position).toBe('fixed');
  });

  it.each(['/chat/', '/workspace/profile/'])('on %s shows the panel glyph, as the desktop Show sidebar does: left_panel_open, 16, secondary text', (path) => {
    pathname = path;
    useMobileSidebarStore.setState({ registeredCount: 1 });
    render(ui());
    const icon = screen.getByRole('button', { name: label }).querySelector('[data-icon]') as HTMLElement;
    expect(icon.dataset.icon).toBe('left_panel_open');
    expect(icon.dataset.size).toBe('16');
    expect(icon.dataset.color).toBe('var(--text-secondary)');
    expect(document.querySelector('[data-icon="menu"]')).toBeNull();
  });

  it('isChatRoute: only /chat and its sub-paths, not look-alikes', () => {
    expect(isChatRoute('/chat')).toBe(true);
    expect(isChatRoute('/chat/')).toBe(true);
    expect(isChatRoute('/chats/')).toBe(false);
    expect(isChatRoute('/workspace/archived-chats/')).toBe(false);
    expect(isChatRoute(null)).toBe(false);
  });

  it('takes focus back when Inbox, opened from the drawer, closes', () => {
    useMobileSidebarStore.setState({ registeredCount: 1 });
    render(ui());
    act(() => useNotificationStore.setState({ isPanelOpen: true }));
    (document.activeElement as HTMLElement | null)?.blur();
    act(() => useNotificationStore.setState({ isPanelOpen: false }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: label }));
  });
});

