/**
 * Another person's answer as the default build draws it for someone reading a chat shared with them: only the owner
 * asks there, so the stop card does not say the reader stopped it, and an approval card is one line on how it stands,
 * naming the owner, with nothing to press.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({ useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }) }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../answer-content', () => ({ AnswerContent: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

const { useChatStore } = await import('../../../store');
const { ChatResponse } = await import('../chat-response');

const stub = (status: string): MessagePart =>
  ({
    type: 'tool_call',
    toolCallId: 'c1',
    stub: true,
    status,
    approval: { approvalId: 'ap-1', serverName: 'Jira', toolName: 'create_issue', toolTitle: 'Create an issue', expiresAt: Date.now() + 15 * 60_000 },
  }) as unknown as MessagePart;

const initial = useChatStore.getState();

beforeEach(() => {
  const slotId = useChatStore.getState().createSlot('c-shared');
  useChatStore.getState().updateSlot(slotId, { isOwner: false });
  useChatStore.setState({
    activeSlotId: slotId,
    sharedConversations: [{ id: 'c-shared', sharedBy: { userId: 'o1', name: 'Owner Person' } } as never],
  });
});

afterEach(() => {
  cleanup();
  useChatStore.setState({ ...initial, slots: {}, activeSlotId: null, sharedConversations: [] });
});

describe("another person's answer, in the default build, for a reader of their chat", () => {
  it('a stopped answer says its asker stopped it, never the reader', () => {
    render(
      <Theme>
        <ChatResponse question="Q" answer="Half an answer" status="stopped" isLastMessage />
      </Theme>,
    );

    const card = screen.getByTestId('chat-stopped-card');
    expect(card.textContent).toContain('The person who asked stopped this response');
    expect(card.textContent).toContain('What was written before it stopped is kept above.');
    expect(card.textContent).not.toContain('You stopped');
  });

  it.each([
    ['awaiting_approval', "Create an issue on Jira is waiting for Owner Person's approval."],
    ['approved', 'Owner Person approved it, so it ran.'],
    ['denied', 'Owner Person declined it.'],
  ])('an approval card %s is one line on how it stands, naming the owner, with nothing to press', (status, line) => {
    render(
      <Theme>
        <ChatResponse question="Q" answer="I will file it." persistedParts={[stub(status), { type: 'text', content: 'I will file it.', isFinal: true }]} isLastMessage />
      </Theme>,
    );

    expect(screen.getByTestId('tool-approval-card').textContent).toBe(line);
    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
  });
});
