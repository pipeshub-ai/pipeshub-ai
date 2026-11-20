/**
 * Someone else's approval card, as the server's reader view gives it to everyone but its asker: a stub of the call
 * (`stub: true`) whose status says how the card stands. It is drawn as one line, never as a step of their run, and the
 * asker's own "Waiting for your approval" line never shows.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: () => ({ hideTabs: false, hideConfidence: false, approvalCardReadOnlyFor: { name: 'Maya Chen' } }),
    },
  };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'q' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({ useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }) }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../answer-content', () => ({ AnswerContent: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';

const WAITING = 'Waiting for your approval to run create_issue on Jira.';
const ANSWER = `I will file it.\n\n${WAITING}`;

const stub = (status: string, expiresAt = Date.now() + 15 * 60_000): MessagePart =>
  ({
    type: 'tool_call',
    toolCallId: 'c1',
    stub: true,
    status,
    approval: { approvalId: 'ap-1', serverName: 'Jira', toolName: 'create_issue', toolTitle: 'Create an issue', expiresAt },
  }) as unknown as MessagePart;

function draw(parts: MessagePart[]) {
  render(
    <Theme>
      <ChatResponse question="Q" answer={ANSWER} persistedParts={[...parts, { type: 'text', content: ANSWER, isFinal: true }]} isLastMessage />
    </Theme>,
  );
}

afterEach(cleanup);

describe("someone else's approval card, drawn from the reader view's stub", () => {
  it.each([
    ['awaiting_approval', "Create an issue on Jira is waiting for Maya Chen's approval."],
    ['approved', 'Maya Chen approved it, so it ran.'],
    ['not_run', "Maya Chen approved it, but it didn't run."],
    ['denied', 'Maya Chen declined it.'],
    ['expired', 'This request expired.'],
  ])('%s: one line, no buttons, no step of their run, and not their waiting line', (status, line) => {
    draw([stub(status)]);

    expect(screen.getByTestId('tool-approval-card').textContent).toBe(line);
    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
    expect(screen.queryByTestId('chat-activity-card')).toBeNull();
    expect(screen.queryByText(/Waiting for your approval/)).toBeNull();
    expect(screen.getByText('I will file it.')).toBeTruthy();
  });

  it('a waiting card that expires while the page is open reads as expired to them too', () => {
    draw([stub('awaiting_approval', Date.now() - 1)]);

    expect(screen.getByTestId('tool-approval-card').textContent).toBe('This request expired.');
  });
});
