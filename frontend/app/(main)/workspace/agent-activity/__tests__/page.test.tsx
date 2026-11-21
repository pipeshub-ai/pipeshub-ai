import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import type { AgentCallEvent } from '../api';

const strings = en.workspace.agentActivity;

// jsdom has no ResizeObserver; Radix measures with it.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean; isProfileInitialized: boolean }) => unknown) =>
    selector({ isAdmin: true, isProfileInitialized: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
  selectIsProfileInitialized: (s: { isProfileInitialized: boolean }) => s.isProfileInitialized,
}));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({ useUserDirectoryEntry: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div>loading</div> }));

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  // Stable like the real hook's `t`, which the page's load callback depends on.
  const translation = {
    t: (key: string, vars?: Record<string, unknown>) => {
      let cur: unknown = locale;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(vars?.[name] ?? ''));
    },
    i18n: { language: 'en-US' },
  };
  return {
    useTranslation: () => translation,
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

const list = vi.fn();
vi.mock('../api', () => ({ AgentActivityApi: { list: (...args: unknown[]) => list(...args) } }));

import AgentActivityPage from '../page';

const call = (over: Partial<AgentCallEvent>): AgentCallEvent => ({
  id: 'a',
  userId: '0123456789abcdef01234567',
  tokenId: 'tok-1',
  tokenKind: 'pat',
  method: 'GET',
  route: '/api/v1/knowledgeBase/:kbId/records',
  statusCode: 200,
  durationMs: 12,
  scopeDenied: false,
  count: 1,
  createdAt: '2026-11-16T10:00:00.000Z',
  ...over,
});

function renderPage() {
  return render(
    <Theme>
      <AgentActivityPage />
    </Theme>,
  );
}

describe('AgentActivityPage', () => {
  beforeEach(() => list.mockReset());
  afterEach(() => cleanup());

  it('shows each call and loads the next page with the cursor', async () => {
    list
      .mockResolvedValueOnce({
        items: [call({ id: 'a', count: 7 }), call({ id: 'b', method: 'POST', statusCode: 403, scopeDenied: true, tokenKind: 'oauth' })],
        nextCursor: 'c1',
      })
      .mockResolvedValueOnce({ items: [call({ id: 'c', route: '/mcp', mcpTool: 'pipeshub_search' })] });

    renderPage();

    expect(await screen.findByText('7 calls')).toBeTruthy();
    expect(screen.getByText(strings.scopeDenied)).toBeTruthy();
    expect(screen.getByText(strings.kindOauth)).toBeTruthy();
    expect(list).toHaveBeenCalledWith({});

    fireEvent.click(screen.getByText(strings.loadMore));

    expect(await screen.findByText('pipeshub_search')).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith({ cursor: 'c1' });
    // Older rows are appended, not swapped in.
    expect(screen.getByText('7 calls')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(strings.loadMore)).toBeNull());
  });

  it('says so when there are no calls', async () => {
    list.mockResolvedValueOnce({ items: [] });

    renderPage();

    expect(await screen.findByText(strings.emptyTitle)).toBeTruthy();
  });

  it("shows the server's sentence from the client's error object", async () => {
    // apiClient rejects with a plain ProcessedError object, not an Error.
    list.mockRejectedValueOnce({ type: 'PERMISSION_ERROR', message: 'Only admins can see agent activity', statusCode: 403 });

    renderPage();

    expect(await screen.findByText('Only admins can see agent activity')).toBeTruthy();
  });

  it('keeps the loaded rows when an older page fails, and retries that page', async () => {
    list
      .mockResolvedValueOnce({ items: [call({ id: 'a', count: 7 })], nextCursor: 'c1' })
      .mockRejectedValueOnce({ type: 'SERVER_ERROR', message: 'The activity store is unavailable', statusCode: 503 })
      .mockResolvedValueOnce({ items: [call({ id: 'c', route: '/mcp', mcpTool: 'pipeshub_search' })] });

    renderPage();

    fireEvent.click(await screen.findByText(strings.loadMore));
    expect(await screen.findByText('The activity store is unavailable')).toBeTruthy();
    expect(screen.getByText('7 calls')).toBeTruthy();

    fireEvent.click(screen.getByText(strings.retry));
    expect(await screen.findByText('pipeshub_search')).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith({ cursor: 'c1' });
    expect(screen.getByText('7 calls')).toBeTruthy();
  });

  it('offers a retry when the list fails to load', async () => {
    list.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ items: [] });

    renderPage();

    fireEvent.click(await screen.findByText(strings.retry));
    expect(await screen.findByText(strings.emptyTitle)).toBeTruthy();
  });
});
