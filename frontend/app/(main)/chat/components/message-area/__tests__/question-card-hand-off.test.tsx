/**
 * A run that stops to ask a question, end to end: the stream through `streamMessageForSlot` into the chat store, and
 * the thread drawn from the store the way the message list draws it (pairs, row keys, `ChatResponse`). Only `fetch`
 * is faked. The card shows the moment its frame arrives, while the run still streams; when the run ends and the saved
 * row replaces the streamed one, the thread still holds that one card, in the same place.
 */
import React, { useMemo } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage, jsonResponse, jwtExpiringIn, sseFrame } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { ConversationMessage, StreamChatRequest } from '../../../types';

installMemoryStorage();

vi.mock('@/config', async (importOriginal) => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  const real = await importOriginal<typeof import('@/config')>();
  return { ...real, useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useChatStore } = await import('../../../store');
const { streamMessageForSlot } = await import('../../../streaming');
const { ChatResponse } = await import('../chat-response');
const { buildMessagePairs } = await import('../message-pairs');
const { useLiveRowAliases } = await import('../row-render-key');
const { chatResponseProps, useSlotStreamingProps } = await import('../chat-response-props');
const { emptyCitationMaps } = await import('../response-tabs/citations');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const RUN = 'run-ask';
const NARRATION = 'I’ll ask which region you mean before I plan anything.';
const REGION = {
  uuid: 'q-region',
  question: 'Which region should the launch cover?',
  multiSelect: false,
  options: [
    { id: 'eu', label: 'EU', isUserInput: false },
    { id: 'us', label: 'US', isUserInput: false },
  ],
};
const CARD = { name: 'ask_user_question', questions: [REGION] };

const frame = (type: string, fields: Record<string, unknown> = {}) => sseFrame(type, { type, ...fields });

/**
 * What the server saved for the turn: the question, the ask, and the reply row that holds the run's steps. Saved at
 * `askedAt`, the time the page sent the question, so the question's time reads the same on both rows.
 */
function savedConversation(askedAt: string) {
  const stored = (overrides: Partial<ConversationMessage>): ConversationMessage =>
    ({
      _id: 'm',
      messageType: 'user_query',
      content: '',
      contentFormat: 'MARKDOWN',
      citations: [],
      followUpQuestions: [],
      feedback: [],
      createdAt: askedAt,
      updatedAt: askedAt,
      ...overrides,
    }) as ConversationMessage;
  return {
    _id: 'conv-ask',
    title: 'Plan the launch',
    createdAt: askedAt,
    updatedAt: askedAt,
    isShared: false,
    status: 'complete',
    modelInfo: { modelKey: 'm1', modelName: 'gpt-5' },
    messages: [
      stored({ _id: 'u1', messageType: 'user_query', content: 'Plan the launch' }),
      stored({ _id: 't1', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: CARD }] } as Partial<ConversationMessage>),
      stored({
        _id: 'b1',
        messageType: 'bot_response',
        content: '',
        parts: [
          { type: 'text', content: NARRATION, settled: true },
          { type: 'tool_call', toolCallId: 'call-ask', toolName: 'internaltools__ask_user_question', status: 'completed' },
        ],
      } as Partial<ConversationMessage>),
    ],
  };
}

/** The run up to its card, then nothing until the test lets the run end with the saved conversation. */
function askThenWait(askedAt: () => string): { release: () => void } {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const asked = [
    frame('RUN_STARTED', { threadId: 'conv-ask', runId: RUN }),
    frame('TEXT_MESSAGE_START', { messageId: 'm1', role: 'assistant', runId: RUN }),
    frame('TEXT_MESSAGE_CONTENT', { messageId: 'm1', delta: NARRATION, runId: RUN }),
    frame('TEXT_MESSAGE_END', { messageId: 'm1', runId: RUN }),
    frame('TOOL_CALL_START', { toolCallId: 'call-ask', toolCallName: 'internaltools__ask_user_question', runId: RUN }),
    frame('TOOL_CALL_ARGS', { toolCallId: 'call-ask', delta: '{}', runId: RUN }),
    frame('CUSTOM', { name: 'ask_user_question', value: { status: 'tool_call', toolData: CARD } }),
  ];
  // The rows make requests of their own while they draw; only the stream gets the run.
  fetchMock.mockImplementation(async (input) => {
    if (!String(input instanceof Request ? input.url : input).includes('/stream')) return jsonResponse(200, {});
    const encoder = new TextEncoder();
    let sent = 0;
    let ended: string[] = [];
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        if (sent === asked.length) {
          await gate;
          ended = [frame('RUN_FINISHED', { runId: RUN, result: { conversation: savedConversation(askedAt()) } })];
        }
        const next = sent < asked.length ? asked[sent] : ended[sent - asked.length];
        sent += 1;
        if (next === undefined) controller.close();
        else controller.enqueue(encoder.encode(next));
      },
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  });
  return { release };
}

const request = (): StreamChatRequest =>
  ({
    query: 'Plan the launch',
    modelKey: 'm1',
    modelName: 'gpt-5',
    modelFriendlyName: 'GPT-5',
    chatMode: 'quick',
    filters: { apps: [], kb: [] },
  }) as StreamChatRequest;

const EMPTY_MAPS = emptyCitationMaps();
const NO_VERSIONS = new Map<string, number>();

/** The thread as the message list draws it: a row per pair, keyed so the saved reply takes the streamed one's place. */
function Thread({ slotId }: { slotId: string }) {
  const slot = useChatStore((s) => s.slots[slotId]);
  const streaming = useSlotStreamingProps(slotId);
  const pairs = useMemo(
    () =>
      buildMessagePairs(slot.messages, {
        isStreaming: slot.isStreaming,
        streamingQuestion: slot.streamingQuestion,
        pendingCollections: slot.pendingCollections,
        regenerateMessageId: slot.regenerateMessageId,
        remoteRunInProgress: slot.remoteRunInProgress,
        emptyCitationMaps: EMPTY_MAPS,
      }),
    [slot.messages, slot.isStreaming, slot.streamingQuestion, slot.pendingCollections, slot.regenerateMessageId, slot.remoteRunInProgress],
  );
  const { renderKeyOf } = useLiveRowAliases(pairs, slotId);
  const ctx = { citationCallbacks: {}, streaming, latestArtifactVersions: NO_VERSIONS, canSend: true, onRetry: () => {} };
  return (
    <Theme>
      {pairs.map((pair, i) => (
        <div key={renderKeyOf(pair.key)} data-row="">
          <ChatResponse {...chatResponseProps(pair, i === pairs.length - 1, ctx)} renderKey={renderKeyOf(pair.key)} />
        </div>
      ))}
    </Theme>
  );
}

const slot = (id: string) => useChatStore.getState().slots[id];
/** When the page sent the question, as its row holds it. */
const askedAt = (id: string) =>
  (slot(id).messages.find((m) => m.role === 'user')?.metadata?.custom as { createdAt?: string } | undefined)?.createdAt ?? '';
const cards = () => screen.queryAllByText('Quick question').map((heading) => heading.closest('.rt-Card') as HTMLElement);
const rows = () => [...document.querySelectorAll('[data-row]')];
/** Everything drawn before `el` (what comes ahead of it and of each element holding it), as markup: what would move it. */
/** What is drawn above `el`, less the question's "Save as command", which only a saved question offers (it needs its id). */
function above(el: HTMLElement): string {
  const drawn: string[] = [];
  for (let node: Element | null = el; node && node !== document.body; node = node.parentElement) {
    for (let before = node.previousElementSibling; before; before = before.previousElementSibling) {
      const copy = before.cloneNode(true) as Element;
      copy.querySelectorAll('[aria-label="Save as command"]').forEach((link) => link.remove());
      drawn.push(copy.outerHTML);
    }
  }
  return drawn.join('\n');
}

beforeEach(() => {
  installBrowserShims();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('a run that stops on a question', () => {
  it('draws the card while the run streams, and keeps that one card in its place when the saved row arrives', async () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });
    const { release } = askThenWait(() => askedAt(slotId));
    render(<Thread slotId={slotId} />);

    const run = streamMessageForSlot(slotId, 'Plan the launch', request());
    await vi.waitFor(() => expect(cards()).toHaveLength(1));
    // The streamed parts reach the store on a time throttle (`scheduleFlush`), so the narration the tool call settled
    // can land a flush after the card: wait for it before reading what is drawn above the card.
    await vi.waitFor(() => expect(screen.getByTestId('chat-activity-card').textContent).toContain(NARRATION));
    expect(slot(slotId).isStreaming).toBe(true);
    const [card] = cards();
    const [row] = rows();
    const drawnAbove = above(card);
    expect(screen.queryByText('Thinking…')).toBeNull();

    await act(async () => {
      release();
      await run;
    });

    expect(slot(slotId).isStreaming).toBe(false);
    expect(slot(slotId).pendingAskUserQuestion).toMatchObject({ assistantMessageId: 'b1', status: 'pending' });
    expect(rows()).toEqual([row]);
    expect(cards()).toEqual([card]);
    expect(screen.getAllByText(/Which region should the launch cover/)).toHaveLength(1);
    expect(above(card)).toBe(drawnAbove);
  });
});
