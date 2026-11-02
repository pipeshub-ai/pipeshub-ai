import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/chat-star-icon', () => ({ ChatStarIcon: () => null }));

const { ChatSection } = await import('../chat-section');

afterEach(() => cleanup());

describe('an empty chat section', () => {
  it('says there are no chats instead of repeating the New chat action', () => {
    renderInTheme(
      <ChatSection
        timeGroups={[]}
        isLoading={false}
        hasError={false}
        currentConversationId={null}
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
        skeletonCount={3}
      />,
    );
    expect(screen.getByText('No chats yet')).toBeTruthy();
    expect(screen.queryByText('Start a chat')).toBeNull();
  });
});
