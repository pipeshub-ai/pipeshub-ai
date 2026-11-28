import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
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
vi.mock('../render-slot', async (importOriginal) => ({ ...(await importOriginal<object>()), useRenderSlotId: () => 'slot-1' }));

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

  it('a check that could not run offers Try again, which reads the chat again, and never Ask again', () => {
    const original = useChatStore.getState().invalidateConversation;
    const invalidateConversation = vi.fn();
    useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false, convId: 'conv-1' } } as never, invalidateConversation });
    try {
      renderRow({ answerHidden: { redacted: true, reason: 'access_check_unavailable' } });

      expect(screen.queryByRole('button', { name: 'Ask again' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
      expect(invalidateConversation).toHaveBeenCalledWith('conv-1');
      expect(streamMessageForSlot).not.toHaveBeenCalled();
    } finally {
      useChatStore.setState({ invalidateConversation: original });
    }
  });

  it('Try again is offered to someone the chat was shared with too: it only reads', () => {
    useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false, isOwner: false, convId: 'conv-1' } } as never });
    renderRow({ answerHidden: { redacted: true, reason: 'access_check_unavailable' } });
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('Ask again sends the same question as a new turn of this chat', () => {
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

    expect(buildRequest).toHaveBeenCalledWith('slot-1', 'What was Q3 revenue?');
    expect(streamMessageForSlot).toHaveBeenCalledWith('slot-1', 'What was Q3 revenue?', { query: 'q', filters: { apps: [], kb: [] } });
  });

  it('Ask again is off while an answer is being written in the chat, and on again after it', () => {
    useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: true, convId: 'conv-1' } } as never });
    renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' } });
    const button = screen.getByRole('button', { name: 'Ask again' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(streamMessageForSlot).not.toHaveBeenCalled();
    act(() => useChatStore.setState({ slots: { 'slot-1': { isStreaming: false, convId: 'conv-1' } } as never }));
    expect(button.disabled).toBe(false);
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

  describe('where the list sends its questions itself (a group chat)', () => {
    const node = (id: string) => ({ id, name: id, nodeType: '', connector: '' });
    const att = { recordId: 'r1', recordName: 'a.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v1' };

    it('Ask again hands it the turn with its own scope and files, and sends nothing itself', () => {
      const onAskAgain = vi.fn();
      const appliedFilters = { apps: [node('app-1')], kb: [] };
      renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' }, onAskAgain, appliedFilters, attachments: [att] });
      fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

      expect(onAskAgain).toHaveBeenCalledExactlyOnceWith({ question: 'What was Q3 revenue?', attachments: [att], collections: undefined, appliedFilters, exactScope: true });
      expect(streamMessageForSlot).not.toHaveBeenCalled();
    });

    it('Ask again waits for the answer being written, as elsewhere', () => {
      const onAskAgain = vi.fn();
      useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: true } } as never });
      renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' }, onAskAgain });
      fireEvent.click(screen.getByRole('button', { name: 'Ask again' }));

      expect(onAskAgain).not.toHaveBeenCalled();
      expect(streamMessageForSlot).not.toHaveBeenCalled();
    });

    it('is offered to the same people: not to someone the chat was shared with, nor for a check that could not run', () => {
      const onAskAgain = vi.fn();
      renderRow({ answerHidden: { redacted: true, reason: 'access_check_unavailable' }, onAskAgain });
      expect(screen.queryByRole('button', { name: 'Ask again' })).toBeNull();
      cleanup();
      useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false, isOwner: false } } as never });
      renderRow({ answerHidden: { redacted: true, reason: 'no_longer_accessible' }, onAskAgain });
      expect(screen.queryByRole('button', { name: 'Ask again' })).toBeNull();
    });
  });

  it('carries the marker of a hidden failure row (type error) to its row', () => {
    const rows = loadHistoricalMessages([
      { _id: 'u', messageType: 'user_query', content: 'Q', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], referenceData: [], modelInfo: {}, createdAt: '', updatedAt: '', feedback: [] },
      { _id: 'e', messageType: 'error', content: 'hidden', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], referenceData: [], modelInfo: {}, createdAt: '', updatedAt: '', feedback: [], answerHidden: { redacted: true, reason: 'no_longer_accessible' } },
    ] as never);
    const custom = rows.messages.find((m) => m.id === 'e')?.metadata?.custom as { answerHidden?: unknown };
    expect(custom.answerHidden).toEqual({ redacted: true, reason: 'no_longer_accessible' });
    // Someone else's failed run reads as any other hidden answer: nothing on the row says it failed.
    expect(custom).not.toHaveProperty('failed');
    expect(custom).not.toHaveProperty('streamError');
  });

  it('draws a hidden answer as its notice, never as a failed reply, whatever code comes with it', () => {
    renderRow({
      answerHidden: { redacted: true, reason: 'no_longer_accessible' },
      streamError: { code: 'answer_failed' },
      failed: true,
    });

    expect(screen.getByTestId('answer-hidden')).toBeTruthy();
    expect(screen.queryByTestId('answer-failed')).toBeNull();
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
