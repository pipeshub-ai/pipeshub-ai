import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { useToastStore } from '@/lib/store/toast-store';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';

installMemoryStorage();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const api = vi.hoisted(() => ({ archiveConversation: vi.fn(), renameConversation: vi.fn() }));
vi.mock('@/chat/api', () => ({ ChatApi: api }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement } = await import('../chat-section-element');

const initialState = useChatStore.getState();
const conversation = {
  id: 'conv-1',
  title: 'Quarterly plan',
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  isShared: false,
  sharedWith: [],
  lastActivityAt: 1000,
  isOwner: true,
} as Conversation;
const toastTitles = () => useToastStore.getState().toasts.map((t) => t.title);

async function chooseFromMenu(name: string) {
  const row = screen.getByRole('link').parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button');
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name }));
  });
}

beforeEach(() => {
  installBrowserShims();
  useToastStore.setState({ toasts: [] });
  api.archiveConversation.mockReset();
  api.renameConversation.mockReset();
  useChatStore.setState({ ...initialState, slots: {}, conversations: [conversation] });
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { isStreaming: true });
});
afterEach(() => cleanup());

describe('sidebar changes while the chat is answering', () => {
  it('tells the user why Rename did nothing', async () => {
    renderInTheme(<ChatSectionElement conversation={conversation} isActive={false} onClick={vi.fn()} />);

    await chooseFromMenu('Rename');

    expect(toastTitles()).toEqual(['Wait for the answer to finish, or press Stop first.']);
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('tells the user why a confirmed archive did nothing, and does not call the API', async () => {
    renderInTheme(<ChatSectionElement conversation={conversation} isActive={false} onClick={vi.fn()} />);

    await chooseFromMenu('Archive');
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' }));
    });

    expect(toastTitles()).toEqual(['Wait for the answer to finish, or press Stop first.']);
    expect(api.archiveConversation).not.toHaveBeenCalled();
  });
});
