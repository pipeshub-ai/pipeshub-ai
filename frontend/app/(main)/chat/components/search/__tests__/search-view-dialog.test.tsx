import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
const api = vi.hoisted(() => ({ fetchConversations: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

import { ChatSearch } from '../search-view';
import { useToastStore } from '@/lib/store/toast-store';
import { ErrorType } from '@/lib/api/api-error';

// jsdom has no layout: report every element as on screen, as a browser would.
beforeEach(() => {
  api.fetchConversations.mockReset().mockResolvedValue({ conversations: [] });
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Harness() {
  const [open, setOpen] = React.useState(false);
  return (
    <Theme>
      <button type="button" onClick={() => setOpen(true)}>
        opener
      </button>
      <ChatSearch open={open} onClose={() => setOpen(false)} />
    </Theme>
  );
}

describe('ChatSearch dialog', () => {
  it('is a modal dialog named Search chats, with a named search field focused on open', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'opener' });
    opener.focus();
    fireEvent.click(opener);

    const dialog = await screen.findByRole('dialog', { name: en.nav.searchChats });
    expect(dialog).toBeTruthy();
    const input = screen.getByRole('textbox', { name: en.nav.searchChats });
    expect(input.getAttribute('placeholder')).toBe(en.nav.searchChatsPlaceholder);
    expect(document.activeElement).toBe(input);
  });

  it('closes on Escape and hands focus back to the opener', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'opener' });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole('dialog', { name: en.nav.searchChats });

    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    // Radix hands focus back on the next tick.
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it('shows the bound New chat shortcut in the shared Kbd chip', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    await screen.findByRole('dialog', { name: en.nav.searchChats });
    const chip = document.querySelector('.rt-Kbd');
    expect(chip?.textContent).toMatch(new RegExp(` \\+ ${en.common.shiftKey} \\+ K$`));
  });

  it('is aria-modal', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    const dialog = await screen.findByRole('dialog', { name: en.nav.searchChats });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
  });

  it('opened with focus on the page body, Escape lands on the Search chats row, never the body', async () => {
    function ShortcutHarness() {
      const [open, setOpen] = React.useState(false);
      React.useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
          if (e.ctrlKey && e.key === 'k') setOpen(true);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
      }, []);
      return (
        <Theme>
          <button type="button">{en.nav.searchChats} Ctrl + K</button>
          <ChatSearch open={open} onClose={() => setOpen(false)} />
        </Theme>
      );
    }
    render(<ShortcutHarness />);
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: en.nav.searchChats });

    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() =>
      expect(document.activeElement?.textContent).toBe(`${en.nav.searchChats} Ctrl + K`),
    );
  });

  it('a failed load shows the approved sentence, never the transport message', async () => {
    api.fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    useToastStore.setState({ toasts: [] } as never);
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    await screen.findByRole('dialog', { name: en.nav.searchChats });
    await waitFor(() => {
      const toasts = (useToastStore.getState() as { toasts: { description?: string }[] }).toasts;
      expect(toasts.map((x) => x.description)).toContain(en.nav.loadChatsFailed);
    });
  });

  it('asks for both lists without the shared error toast (it shows its own)', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    await screen.findByRole('dialog', { name: en.nav.searchChats });
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));
    for (const call of api.fetchConversations.mock.calls) {
      expect(call[2]).toMatchObject({ suppressErrorToast: true });
    }
  });
  it('typing while the first list loads shows loading, not No results and not an error toast (DF-336)', async () => {
    // The API client rejects an aborted request with a processed error, not an AbortError.
    api.fetchConversations.mockImplementation(
      (_page: number, _limit: number, opts: { signal: AbortSignal; search?: string }) =>
        new Promise((_resolve, reject) => {
          opts.signal.addEventListener('abort', () =>
            reject({ type: ErrorType.REQUEST_CANCELLED, message: 'Request was cancelled.' }),
          );
        }),
    );
    useToastStore.setState({ toasts: [] } as never);
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    const input = await screen.findByRole('textbox', { name: en.nav.searchChats });
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));

    fireEvent.change(input, { target: { value: 'budget' } });
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(4));

    expect(screen.queryByText(en.message.noResults)).toBeNull();
    expect((useToastStore.getState() as { toasts: unknown[] }).toasts).toHaveLength(0);
  });

  it('reopening never starts a search for the last query: its first lists ask with no search', async () => {
    useToastStore.setState({ toasts: [] } as never);
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    const input = await screen.findByRole('textbox', { name: en.nav.searchChats });
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(2));

    fireEvent.change(input, { target: { value: 'budget' } });
    await waitFor(() => expect(api.fetchConversations).toHaveBeenCalledTimes(4));
    expect(api.fetchConversations.mock.calls[2][2]).toMatchObject({ search: 'budget' });

    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const callsBeforeReopen = api.fetchConversations.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    const reopened = await screen.findByRole('textbox', { name: en.nav.searchChats });
    expect((reopened as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(api.fetchConversations.mock.calls.length).toBe(callsBeforeReopen + 2));
    for (const call of api.fetchConversations.mock.calls.slice(callsBeforeReopen)) {
      expect(call[2]).not.toHaveProperty('search');
      expect((call[2] as { signal: AbortSignal }).signal.aborted).toBe(false);
    }
    expect((useToastStore.getState() as { toasts: unknown[] }).toasts).toHaveLength(0);
  });
});

describe('ChatSearch list size and keys', () => {
  const now = new Date().toISOString();
  const chat = (id: string, title: string) => ({
    id, title, createdAt: now, updatedAt: now, isShared: false, sharedWith: [], isOwner: true,
  });

  beforeEach(() => {
    api.fetchConversations.mockImplementation((_page: number, _limit: number, opts: { source: string }) =>
      Promise.resolve({
        conversations: opts.source === 'owned' ? [chat('c1', 'Budget review'), chat('c2', 'Launch plan')] : [],
      }),
    );
  });

  /** Opens the dialog with two of today's chats; returns [New chat, chat 1, chat 2]. */
  async function openWithChats() {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'opener' }));
    const dialog = await screen.findByRole('dialog', { name: en.nav.searchChats });
    await screen.findByRole('button', { name: 'Launch plan' });
    return { dialog, rows: Array.from(dialog.querySelectorAll<HTMLElement>('button.cmdk-row')) };
  }

  it('caps the panel at 444px and 64px above the window bottom, instead of running to the bottom', async () => {
    const { dialog } = await openWithChats();
    expect(dialog.style.top).toBe('28%');
    expect(dialog.style.maxHeight).toBe(
      'min(calc(var(--space-9) * 7 - var(--space-1)), calc(72dvh - var(--space-9)))',
    );
  });

  it('chat rows sit flush, with no gap between them', async () => {
    const { rows } = await openWithChats();
    expect(rows[1].parentElement).toBe(rows[2].parentElement);
    expect(rows[1].parentElement?.className ?? '').not.toMatch(/rt-r-gap/);
  });

  it('up and down move between the field, New chat and the chats', async () => {
    const { rows } = await openWithChats();
    const [newChat, first, second] = rows;
    const input = screen.getByRole('textbox', { name: en.nav.searchChats });
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(input);
    fireEvent.keyDown(input, { key: 'ArrowDown', shiftKey: true });
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(newChat);
    fireEvent.keyDown(newChat, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(second);

    fireEvent.keyDown(second, { key: 'ArrowUp' });
    fireEvent.keyDown(first, { key: 'ArrowUp' });
    fireEvent.keyDown(newChat, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(input);
  });

  it('a row that takes focus is scrolled into view by the least amount', async () => {
    const { rows } = await openWithChats();
    const last = rows[rows.length - 1];
    // Radix wraps Shift+Tab from the field to the last row with a focus that does not scroll.
    last.focus({ preventScroll: true });
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    expect(vi.mocked(Element.prototype.scrollIntoView).mock.contexts.at(-1)).toBe(last);
  });
});
