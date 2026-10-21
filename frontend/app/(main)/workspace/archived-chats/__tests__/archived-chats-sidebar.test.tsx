/**
 * The archive shelf's row menu: Restore for every chat, Permanently delete only for chats the user
 * owns, and the menu reachable from the keyboard without selecting the row.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/config', () => ({ chatExtension: { formatConversationTitle: (title: string | undefined) => title } }));
vi.mock('../api', () => ({ ArchivedChatsApi: {} }));

import { ArchivedChatsSidebar } from '../components/archived-chats-sidebar';

const chat = (id: string, title: string, isOwner: boolean) => ({ id, title, isOwner }) as Conversation;

const onSelect = vi.fn();

function renderShelf() {
  return renderInTheme(
    <ArchivedChatsSidebar
      conversations={[chat('c-mine', 'My plan', true), chat('c-shared', 'Shared plan', false)]}
      agentGroups={[]}
      isLoading={false}
      isLoadingAgentGroups={false}
      selectedConversationId={null}
      selectedAgentKey={null}
      onSelect={onSelect}
      onSearchClick={() => {}}
      onLoadMoreForAgent={() => {}}
      assistantChatsTotalCount={2}
      assistantChatsHasMore={false}
      isLoadingMoreAssistantChats={false}
      onLoadMoreAssistantChats={() => {}}
      agentGroupsTotalCount={0}
      agentGroupsHasMore={false}
      isLoadingMoreAgentGroups={false}
      onLoadMoreAgentGroups={() => {}}
      onRestored={() => {}}
      onDeleted={() => {}}
    />,
  );
}

const row = (title: string) => screen.getByText(title).closest('[role="button"]') as HTMLElement;

/** Hovers the row and opens its menu with the keyboard; returns the entries it offers. */
async function menuEntries(title: string): Promise<string[]> {
  fireEvent.mouseEnter(row(title));
  fireEvent.keyDown(screen.getByRole('button', { name: 'More options' }), { key: 'Enter' });
  return (await screen.findAllByRole('menuitem')).map((item) => item.textContent ?? '');
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the archive shelf row menu', () => {
  it('offers Permanently delete on a chat the user owns', async () => {
    renderShelf();
    expect(await menuEntries('My plan')).toEqual(['Restore', 'Permanently Delete']);
  });

  it('offers only Restore on a chat someone else owns', async () => {
    renderShelf();
    expect(await menuEntries('Shared plan')).toEqual(['Restore']);
  });

  it('shows on keyboard focus and opens without selecting the row', async () => {
    renderShelf();

    act(() => row('My plan').focus());
    const trigger = screen.getByRole('button', { name: 'More options' });
    act(() => trigger.focus());
    fireEvent.keyDown(trigger, { key: 'Enter' });

    expect(await screen.findAllByRole('menuitem')).toHaveLength(2);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
