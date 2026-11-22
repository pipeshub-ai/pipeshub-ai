import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import { buildMessagePairs } from '../message-pairs';
import { loadHistoricalMessages } from '../../../runtime';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
const { streamMessageForSlot, buildRequest } = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildRequest: vi.fn(() => ({ query: 'q' })),
}));
vi.mock('../../../streaming', () => ({ streamMessageForSlot }));
vi.mock('../../../runtime', async (importOriginal) => ({ ...(await importOriginal<object>()), buildStreamChatRequestForSlot: buildRequest }));
vi.mock('../render-slot', () => ({ useRenderSlotId: () => 'slot-1' }));

import { ChatResponse } from '../chat-response';

const renderRow = (props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) =>
  renderInTheme(
    <ChatResponse
      question="What was Q3 revenue?"
      answer="This answer used content you can no longer access."
      messageId="msg-1"
      isLastMessage
      {...props}
    />,
  );

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false } } as never });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('ChatResponse hidden answer', () => {
  it('shows the question and the lost-access notice instead of the answer actions', () => {
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });

    expect(screen.getByText('What was Q3 revenue?')).toBeTruthy();
    expect(screen.getByTestId('answer-hidden').textContent).toContain('This answer used content you can no longer access.');
    expect(screen.queryByLabelText('Regenerate')).toBeNull();
  });

  it('says access could not be confirmed when the check was unavailable', () => {
    renderRow({ answerHidden: { redacted: true, reason: 'access_check_unavailable' } });
    expect(screen.getByTestId('answer-hidden').textContent).toContain("We couldn't confirm your access. Try again.");
  });

  it('Ask again sends the same question as a new turn of this chat', () => {
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

    expect(buildRequest).toHaveBeenCalledWith('slot-1', 'What was Q3 revenue?');
    expect(streamMessageForSlot).toHaveBeenCalledWith('slot-1', 'What was Q3 revenue?', { query: 'q', filters: { apps: [], kb: [] } });
  });

  it('Ask again resends the hidden turn\'s own filters and files, not the composer\'s', () => {
    const node = (id: string) => ({ id, name: id, nodeType: '', connector: '' });
    const att = { recordId: 'r1', recordName: 'a.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v1' };
    const request: Record<string, unknown> = { query: 'q', filters: { apps: ['composer'], kb: [] } };
    buildRequest.mockReturnValueOnce(request as never);
    renderRow({
      answerHidden: { redacted: true, reason: 'no_longer_accessible' },
      appliedFilters: { apps: [node('app-1')], kb: [node('kb-1')], records: [node('rec-1')] },
      attachments: [att],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

    expect(request.filters).toEqual({ apps: ['app-1'], kb: ['kb-1'], records: ['rec-1'] });
    expect(request.attachments).toEqual([att]);
  });

  it('Ask again resets to no filters when the original turn had none', () => {
    const request: Record<string, unknown> = { query: 'q', filters: { apps: ['composer'], kb: ['picked'] }, appliedFilters: { apps: [], kb: [] } };
    buildRequest.mockReturnValueOnce(request as never);
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

    expect(request.filters).toEqual({ apps: [], kb: [] });
    expect(request).not.toHaveProperty('appliedFilters');
  });

  it('carries the marker of a hidden failure row (type error) to its row', () => {
    const rows = loadHistoricalMessages([
      { _id: 'u', messageType: 'user_query', content: 'Q', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], referenceData: [], modelInfo: {}, createdAt: '', updatedAt: '', feedback: [] },
      { _id: 'e', messageType: 'error', content: 'hidden', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], referenceData: [], modelInfo: {}, createdAt: '', updatedAt: '', feedback: [], answerHidden: { redacted: true, reason: 'no_longer_accessible' } },
    ] as never);
    const custom = rows.messages.find((m) => m.id === 'e')?.metadata?.custom as { answerHidden?: unknown };
    expect(custom.answerHidden).toEqual({ redacted: true, reason: 'no_longer_accessible' });
  });

  it('shows the notice without Ask again to someone the chat was shared with', () => {
    useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false, isOwner: false } } as never });
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });

    expect(screen.getByTestId('answer-hidden').textContent).toContain('no longer access');
    expect(screen.queryByRole('button', { name: 'Ask again' })).toBeNull();
  });

  it('shows the notice without Ask again inside a Compare column', () => {
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' }, columnMode: true });

    expect(screen.getByTestId('answer-hidden')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Ask again' })).toBeNull();
  });

  it('shows no notice for an ordinary answer', () => {
    renderRow({ answer: 'Revenue was 4M.' });
    expect(screen.queryByTestId('answer-hidden')).toBeNull();
  });
});

describe('buildMessagePairs hidden answer', () => {
  it('carries the marker from the stored message to its row', () => {
    const marker = { redacted: true, reason: 'no_longer_accessible' } as const;
    const pairs = buildMessagePairs(
      [
        { id: 'u', role: 'user', content: [{ type: 'text', text: 'Q' }] },
        { id: 'a', role: 'assistant', content: [{ type: 'text', text: 'x' }], metadata: { custom: { messageId: 'a', answerHidden: marker } } },
      ],
      { isStreaming: false, streamingQuestion: '', pendingCollections: [], regenerateMessageId: null, emptyCitationMaps: {} as never },
    );
    expect(pairs[0].answerHidden).toEqual(marker);
  });
});
