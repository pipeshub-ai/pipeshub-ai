import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/knowledge-base/__tests__/kb-page-harness';
import { ArchivedChatsSidebar } from '../components/archived-chats-sidebar';

const canHover = vi.hoisted(() => ({ value: true }));
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => canHover.value }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

beforeAll(installBrowserShims);
beforeEach(() => {
  canHover.value = true;
});
afterEach(cleanup);

function renderSidebar() {
  renderInTheme(
    <ArchivedChatsSidebar
      conversations={[{ id: 'c1', title: 'Old chat' } as never]}
      agentGroups={[]}
      isLoading={false}
      isLoadingAgentGroups={false}
      selectedConversationId={null}
      selectedAgentKey={null}
      onSelect={() => {}}
      onSearchClick={() => {}}
      onLoadMoreForAgent={() => {}}
      assistantChatsTotalCount={1}
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

describe('archived chat row actions', () => {
  it('are hidden until hover on a mouse device', () => {
    renderSidebar();
    expect(screen.getByText('Old chat')).toBeTruthy();
    const before = screen.queryAllByRole('button').length;
    cleanup();
    canHover.value = false;
    renderSidebar();
    expect(screen.getAllByRole('button').length).toBe(before + 1);
  });
});
