import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

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
vi.mock('@/app/components/ui/user-avatar', () => ({ UserAvatar: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ChatSidebarHeader } from '../header';
import { useChatStore } from '@/chat/store';

// Zustand copies actions into every new state, so a spy would outlive its test; swap a stub in instead.
const originalClearSearchResults = useChatStore.getState().clearSearchResults;
function stubClearSearchResults() {
  const stub = vi.fn();
  useChatStore.setState({ clearSearchResults: stub });
  return stub;
}

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
