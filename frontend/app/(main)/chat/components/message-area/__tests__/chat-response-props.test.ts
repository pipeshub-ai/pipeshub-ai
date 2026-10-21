/**
 * The answer props every list shares: `chatResponseProps` maps a pair exactly as the message list
 * always has, the live fields reach only the streaming answer, and the artifact versions Map keeps
 * its identity while no version changes.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ThreadMessageLike } from '@assistant-ui/react';

// `../../../store` pulls in `@/lib/api`, whose auth store reads localStorage at import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { chatResponseProps, useLatestArtifactVersions, useSlotStreamingProps } = await import('../chat-response-props');
const { useChatStore } = await import('../../../store');
import type { ChatResponseContext, SlotStreamingProps } from '../chat-response-props';
import type { MessagePair } from '../message-pairs';
import type { ChatArtifact } from '../../../types';

const initialState = useChatStore.getState();

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null });
});

const citationMaps = { citations: {}, sources: {}, sourcesOrder: [], citationsOrder: {}, citationToNumber: {} } as unknown as MessagePair['citationMaps'];

const live: SlotStreamingProps = {
  streamingContent: 'Revenue was',
  currentStatusMessage: { id: 's', status: 'processing', message: 'Searching', timestamp: '' },
  streamingCitationMaps: citationMaps,
  streamingArtifacts: [],
  streamingParts: [],
};

function context(overrides: Partial<ChatResponseContext> = {}): ChatResponseContext {
  return {
    citationCallbacks: { onPreview: vi.fn() },
    streaming: live,
    latestArtifactVersions: new Map(),
    canSend: true,
    onRetry: vi.fn(),
    ...overrides,
  };
}

const settled: MessagePair = {
  key: 'a1',
  question: 'What was Q3 revenue?',
  answer: '$4.2M.',
  citationMaps,
  isStreaming: false,
  messageId: 'a1',
  createdAt: '2026-10-01T00:00:00.000Z',
  confidence: 'High',
  status: 'stopped',
  failed: false,
};

describe('chatResponseProps', () => {
  it('maps a settled pair as the message list does, with no live fields', () => {
    const ctx = context();
    const layout = { compact: true };

    expect(chatResponseProps(settled, true, ctx, layout)).toEqual({
      question: 'What was Q3 revenue?',
      answer: '$4.2M.',
      citationMaps,
      citationCallbacks: ctx.citationCallbacks,
      confidence: 'High',
      isStreaming: false,
      modelInfo: undefined,
      collections: undefined,
      appliedFilters: undefined,
      attachments: undefined,
      messageId: 'a1',
      isLastMessage: true,
      rowId: 'a1',
      citationMessageRowKey: 'a1',
      createdAt: '2026-10-01T00:00:00.000Z',
      streamingContent: undefined,
      currentStatusMessage: undefined,
      streamingCitationMaps: undefined,
      streamingArtifacts: undefined,
      streamingParts: undefined,
      latestArtifactVersions: ctx.latestArtifactVersions,
      persistedParts: undefined,
      persistedAskUserQuestion: undefined,
      persistedAskUserQuestionAnswers: undefined,
      feedbackInfo: undefined,
      status: 'stopped',
      streamError: undefined,
      failed: false,
      onRetry: ctx.onRetry,
      canSend: true,
      unanswered: undefined,
      pair: settled,
      layout,
    });
  });

  it('gives the live fields to the answer that streams', () => {
    const props = chatResponseProps({ ...settled, isStreaming: true }, true, context());

    expect(props).toMatchObject({
      isStreaming: true,
      streamingContent: 'Revenue was',
      currentStatusMessage: live.currentStatusMessage,
      streamingCitationMaps: citationMaps,
      streamingArtifacts: live.streamingArtifacts,
      streamingParts: live.streamingParts,
    });
    expect(props.layout).toBeUndefined();
  });

  it('offers no retry to someone who cannot send', () => {
    const props = chatResponseProps(settled, true, context({ canSend: false }));

    expect(props.onRetry).toBeUndefined();
    expect(props.canSend).toBe(false);
  });
});

function answer(id: string, text: string): ThreadMessageLike {
  return { id, role: 'assistant', content: [{ type: 'text', text }] };
}

const marker = (recordId: string, version: number) =>
  `::artifact[${recordId}.csv](record:${recordId}){text/csv|doc-${recordId}|${recordId}|SPREADSHEET|${version}}`;

describe('useLatestArtifactVersions', () => {
  it('keeps the highest version of each record, counting the live artifacts while they stream', () => {
    const messages = [answer('a1', `First.\n${marker('rec-1', 1)}`), answer('a2', `Then.\n${marker('rec-1', 3)}`)];
    const liveArtifact = { recordId: 'rec-2', version: 2 } as ChatArtifact;

    const settledOnly = renderHook(() => useLatestArtifactVersions(messages, null)).result.current;
    const streaming = renderHook(() => useLatestArtifactVersions(messages, [liveArtifact])).result.current;

    expect([...settledOnly]).toEqual([['rec-1', 3]]);
    expect([...streaming]).toEqual([['rec-1', 3], ['rec-2', 2]]);
  });

  it('keeps the same Map until a version changes', () => {
    const first = [answer('a1', `Here.\n${marker('rec-1', 1)}`)];
    const { result, rerender } = renderHook(({ messages }) => useLatestArtifactVersions(messages, null), {
      initialProps: { messages: first },
    });
    const before = result.current;

    rerender({ messages: [...first, answer('a2', 'No files this time.')] });
    expect(result.current).toBe(before);

    rerender({ messages: [...first, answer('a3', `Updated.\n${marker('rec-1', 2)}`)] });
    expect(result.current).not.toBe(before);
    expect(result.current.get('rec-1')).toBe(2);
  });
});

describe('useSlotStreamingProps', () => {
  it('reads the named slot, not the active one, and keeps its value while nothing changes', () => {
    const store = useChatStore.getState();
    const active = store.createSlot('conv-active');
    const other = store.createSlot('conv-other');
    store.setActiveSlot(active);
    store.updateSlot(active, { streamingContent: 'active text' });
    store.updateSlot(other, { streamingContent: 'other text' });

    const { result } = renderHook(() => useSlotStreamingProps(other));
    const before = result.current;
    expect(before.streamingContent).toBe('other text');

    act(() => useChatStore.getState().updateSlot(active, { streamingContent: 'active text, more' }));
    expect(result.current).toBe(before);

    act(() => useChatStore.getState().updateSlot(other, { streamingContent: 'other text, more' }));
    expect(result.current.streamingContent).toBe('other text, more');
  });

  it('gives empty values without a slot', () => {
    const { result } = renderHook(() => useSlotStreamingProps(null));

    expect(result.current).toEqual({
      streamingContent: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      streamingArtifacts: [],
      streamingParts: [],
    });
  });
});
