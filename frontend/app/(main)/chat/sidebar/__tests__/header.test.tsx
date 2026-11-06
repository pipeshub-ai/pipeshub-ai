import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const state = vi.hoisted(() => ({ drawer: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => state.drawer, useIsBelowDesktop: () => state.drawer }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, onClick, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault();
        onClick?.(e);
      }}
      {...rest}
    >
      {children}
    </a>
  ),
}));
vi.mock('@/config', () => ({ BrandMark: () => <span>logo</span> }));

import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { ChatSidebarHeader, SidebarHeaderActions } from '../header';
import { useChatStore } from '@/chat/store';

// Zustand copies actions into every new state, so a spy would outlive its test; swap a stub in instead.
const originalClearSearchResults = useChatStore.getState().clearSearchResults;
function stubClearSearchResults() {
  const stub = vi.fn();
  useChatStore.setState({ clearSearchResults: stub });
  return stub;
}

const ui = () => (
  <Theme>
    <SidebarHeaderActions />
  </Theme>
);

beforeEach(() => {
  state.drawer = false;
  useNotificationStore.setState({ unreadCount: 0, isPanelOpen: false });
  useSidebarWidthStore.getState().setNavCollapsed(false);
});
afterEach(() => {
  cleanup();
  useChatStore.setState({ clearSearchResults: originalClearSearchResults });
  useChatStore.getState().setMode('chat');
});

function renderHeader() {
  return render(
    <Theme>
      <ChatSidebarHeader />
    </Theme>,
  );
}

describe('sidebar header actions', () => {
  it('names Inbox with its unread count, and shows a solid red badge on it', () => {
    useNotificationStore.setState({ unreadCount: 12 });
    render(ui());
    const inbox = screen.getByRole('button', { name: 'Inbox, 12 unread' });
    const badge = inbox.querySelector('.rt-Badge');
    expect(badge?.textContent).toBe('9+');
    expect(badge?.classList.contains('rt-variant-solid')).toBe(true);
    expect(badge?.getAttribute('aria-hidden')).toBe('true');
  });

  it('names Inbox plainly with nothing unread', () => {
    render(ui());
    expect(screen.getByRole('button', { name: 'Inbox' }).querySelector('.rt-Badge')).toBeNull();
  });

  it('names the collapse control Hide sidebar, and collapses the rail', () => {
    render(ui());
    fireEvent.click(screen.getByRole('button', { name: 'Hide sidebar' }));
    expect(useSidebarWidthStore.getState().isNavCollapsed).toBe(true);
  });

  it('in the drawer layout, has no Hide sidebar, and Inbox closes the drawer first', () => {
    state.drawer = true;
    act(() => useMobileSidebarStore.getState().open());
    render(ui());
    expect(screen.queryByRole('button', { name: 'Hide sidebar' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Inbox' }));
    expect(useMobileSidebarStore.getState().isOpen).toBe(false);
    expect(useNotificationStore.getState().isPanelOpen).toBe(true);
  });

  it('holds only Home, Inbox and Hide sidebar, with no profile link', () => {
    const { container } = render(
      <Theme>
        <ChatSidebarHeader />
      </Theme>,
    );
    expect(screen.queryByRole('link', { name: 'Open profile' })).toBeNull();
    expect(container.querySelector('a[href*="/workspace/profile"]')).toBeNull();
    const controls = Array.from(container.querySelectorAll('button, a[href]'));
    expect(controls.map((el) => el.getAttribute('aria-label'))).toEqual(['Home', 'Inbox', 'Hide sidebar']);
  });
});

describe('Chat sidebar header: Home', () => {
  it('links the logo to /chat/', () => {
    renderHeader();
    expect(screen.getByRole('link', { name: 'Home' }).getAttribute('href')).toBe('/chat/');
  });

  it('leaves search results when Home is clicked, since both live on /chat/', () => {
    const clearSearchResults = stubClearSearchResults();
    useChatStore.getState().setMode('search');
    renderHeader();

    fireEvent.click(screen.getByRole('link', { name: 'Home' }));

    expect(useChatStore.getState().settings.mode).toBe('chat');
    expect(clearSearchResults).toHaveBeenCalledOnce();
  });

  it('keeps the search in this tab when Home is opened with a modifier key', () => {
    const clearSearchResults = stubClearSearchResults();
    useChatStore.getState().setMode('search');
    renderHeader();

    fireEvent.click(screen.getByRole('link', { name: 'Home' }), { metaKey: true });
    fireEvent.click(screen.getByRole('link', { name: 'Home' }), { ctrlKey: true });

    expect(useChatStore.getState().settings.mode).toBe('search');
    expect(clearSearchResults).not.toHaveBeenCalled();
  });
});
