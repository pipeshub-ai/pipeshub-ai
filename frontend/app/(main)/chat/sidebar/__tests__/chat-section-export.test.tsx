import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const exporter = vi.hoisted(() => ({ exportConversation: vi.fn() }));
vi.mock('@/chat/utils/export-conversation', () => exporter);

import { ChatSectionElement } from '../chat-section-element';

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'conv-1',
    title: 'Quarterly plan',
    isOwner: true,
    ...overrides,
  } as Conversation;
}

function toastLines() {
  return useToastStore.getState().toasts.map((t) => `${t.variant}:${t.title}`);
}

async function exportFromMenu(title: string) {
  const row = screen.getByRole('link', { name: title }).parentElement as HTMLElement;
  fireEvent.mouseEnter(row);
  const trigger = within(row).getByRole('button');
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export' }));
  });
}

beforeEach(() => {
  installBrowserShims();
  useToastStore.setState({ toasts: [] });
  exporter.exportConversation.mockReset();
});
afterEach(() => cleanup());

describe('exporting a chat from the sidebar', () => {
  it('exports the chat with the words for the current language and says when it is done', async () => {
    exporter.exportConversation.mockResolvedValue(undefined);
    renderInTheme(<ChatSectionElement conversation={conversation()} isActive={false} onClick={vi.fn()} />);

    await exportFromMenu('Quarterly plan');

    await waitFor(() => expect(toastLines()).toEqual(['success:Chat exported']));
    expect(exporter.exportConversation).toHaveBeenCalledWith({
      conversationId: 'conv-1',
      title: 'Quarterly plan',
      agentId: undefined,
      labels: { you: 'You', assistant: 'Assistant', sources: 'Sources' },
    });
  });

  it('reads an agent chat from the agent', async () => {
    exporter.exportConversation.mockResolvedValue(undefined);
    renderInTheme(
      <ChatSectionElement conversation={conversation()} isActive={false} onClick={vi.fn()} agentId="agent-7" />,
    );

    await exportFromMenu('Quarterly plan');

    await waitFor(() => expect(exporter.exportConversation).toHaveBeenCalledTimes(1));
    expect(exporter.exportConversation.mock.calls[0][0]).toMatchObject({ conversationId: 'conv-1', agentId: 'agent-7' });
  });

  it('shows one error and nothing else when the chat cannot be read', async () => {
    exporter.exportConversation.mockRejectedValue(new Error('network down'));
    renderInTheme(<ChatSectionElement conversation={conversation()} isActive={false} onClick={vi.fn()} />);

    await exportFromMenu('Quarterly plan');

    await waitFor(() => expect(toastLines()).toEqual(['error:Could not export the chat']));
  });

  it('shows a loading message while the chat is being collected', async () => {
    let finish: () => void = () => {};
    exporter.exportConversation.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
    renderInTheme(<ChatSectionElement conversation={conversation()} isActive={false} onClick={vi.fn()} />);

    await exportFromMenu('Quarterly plan');

    expect(toastLines()).toEqual(['loading:Exporting chat…']);
    await act(async () => finish());
    await waitFor(() => expect(toastLines()).toEqual(['success:Chat exported']));
  });

  it('does not offer export on a chat someone else shared', () => {
    renderInTheme(
      <ChatSectionElement conversation={conversation({ isOwner: false })} isActive={false} onClick={vi.fn()} />,
    );

    const row = screen.getByRole('link', { name: /Quarterly plan/ }).parentElement as HTMLElement;
    fireEvent.mouseEnter(row);

    expect(within(row).queryByRole('button')).toBeNull();
  });
});
