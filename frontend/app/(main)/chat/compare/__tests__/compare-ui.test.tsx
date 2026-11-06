import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { fakeApi } from '@/lib/api/__tests__/fake-api';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import { useAuthStore } from '@/lib/store/auth-store';
import type { AskUserQuestionPayload, AvailableLlmModel, ChatSlot } from '../../types';

installMemoryStorage();

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../streaming', () => ({ streamMessageForSlot: vi.fn(), cancelStreamForSlot: vi.fn() }));

import { ChatResponse } from '../../components/message-area/chat-response';
import { CompareColumns } from '../components/compare-columns';
import { CompareToggle, useCompareAvailable } from '../components/compare-picker-toggle';
import { useCompareStore } from '../compare-store';
import { ASSISTANT_CTX, useChatStore } from '../../store';
import { cancelStreamForSlot, streamMessageForSlot } from '../../streaming';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { CompareTurn } from '../types';

const A = { modelKey: 'ka', modelName: 'model-a', modelFriendlyName: 'Model A', modelProvider: 'openAI' };
const B = { modelKey: 'kb', modelName: 'model-b', modelFriendlyName: 'Model B', modelProvider: 'anthropic' };
const C = { modelKey: 'kc', modelName: 'model-c', modelFriendlyName: 'Model C', modelProvider: 'google' };
const llm = (m: typeof A): AvailableLlmModel =>
  ({ modelType: 'llm', provider: m.modelProvider, modelKey: m.modelKey, modelName: m.modelName, modelFriendlyName: m.modelFriendlyName }) as AvailableLlmModel;

const PAYLOAD = (question: string): AskUserQuestionPayload => ({
  name: 'ask_user_question',
  questions: [
    {
      uuid: 'q1',
      question,
      multiSelect: false,
      options: [
        { id: 'o1', label: 'EU', isUserInput: false },
        { id: 'o2', label: 'US', isUserInput: false },
      ],
    },
  ],
});

type Side = 'done' | 'streaming' | 'card' | 'error' | 'empty';
const rowsFor = (prefix: string, side: Side): ChatSlot['messages'] => [
  { id: `${prefix}-u`, role: 'user', content: [{ type: 'text', text: 'Pick a region' }] },
  {
    id: `${prefix}-a`,
    role: 'assistant',
    content: [{ type: 'text', text: side === 'done' ? `${prefix} answer` : side === 'error' ? 'boom' : '' }],
    metadata: { custom: side === 'error' ? {} : { messageId: `${prefix}-bot` } },
  },
];

function seed(opts: { left: Side; right: Side | 'none'; phase?: CompareTurn['phase']; template?: boolean }) {
  const slotPatch = (prefix: string, side: Side): Partial<ChatSlot> => ({
    messages: rowsFor(prefix, side),
    isStreaming: side === 'streaming',
    isInitialized: true,
    isOwner: true,
    ...(side === 'card'
      ? {
          pendingAskUserQuestion: {
            assistantMessageId: `${prefix}-a`,
            payload: PAYLOAD(`Which region for ${prefix}?`),
            answers: {},
            status: 'pending' as const,
          },
        }
      : {}),
  });
  const store = useChatStore.getState();
  const primary = store.createSlot('conv-1');
  store.updateSlot(primary, slotPatch('left', opts.left));
  useChatStore.setState({ activeSlotId: primary });
  let branchSlotId: string | null = null;
  if (opts.right !== 'none') {
    branchSlotId = store.createSlot('branch-1');
    store.updateSlot(branchSlotId, slotPatch('right', opts.right));
  }
  const turn: CompareTurn = {
    turnId: 1,
    models: [A, B],
    displayQuery: 'Pick a region',
    template: opts.template === false ? null : ({ query: 'Pick a region', chatMode: 'agent:auto', filters: { apps: [], kb: [] }, ...B } as never),
    userMessageId: 'u1',
    parentConvId: 'conv-1',
    branchConvId: branchSlotId ? 'branch-1' : null,
    branchSlotId,
    phase: opts.phase ?? 'ready',
    choosing: false,
    chooseError: null,
  };
  useCompareStore.setState({ turns: { [primary]: turn } });
  return { primary, branchSlotId, turn };
}

function renderColumns(primary: string, turn: CompareTurn) {
  const left = (
    <ChatResponse
      columnMode
      question="Pick a region"
      answer={useChatStore.getState().slots[primary].isStreaming ? '' : 'left answer'}
      messageId="left-bot"
      isLastMessage
      rowId="left-a"
      citationMessageRowKey="left-a"
      isStreaming={useChatStore.getState().slots[primary].isStreaming}
    />
  );
  // The message list passes the turn it reads from the store, so the view follows its updates.
  function Live() {
    const current = useCompareStore((s) => s.turns[primary]) ?? turn;
    return (
      <CompareColumns
        slotId={primary}
        turn={current}
        question={<div data-testid="question">Pick a region</div>}
        primary={left}
      />
    );
  }
  return renderInTheme(<Live />);
}

const columns = () => screen.getAllByTestId('compare-column');
const useButtons = () => screen.queryAllByRole('button', { name: /^Use the answer from/ });

beforeEach(() => {
  installBrowserShims();
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  vi.mocked(streamMessageForSlot).mockReset();
  vi.mocked(cancelStreamForSlot).mockReset();
  useCompareStore.setState({ enabled: false, models: [], turns: {} });
  useChatStore.setState({ slots: {}, activeSlotId: null });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Compare toggle availability', () => {
  const setup = (over: { flag?: boolean; models?: typeof A[]; mode?: 'agent' | 'chat' | 'web-search' | 'image' } = {}) => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_COMPARE: over.flag ?? true } });
    const chat = useChatStore.getState();
    chat.setAvailableModelsForCtx(ASSISTANT_CTX, (over.models ?? [A, B]).map(llm));
    chat.setQueryMode(over.mode ?? 'agent');
  };
  const available = (ctx: string = ASSISTANT_CTX) => renderHook(() => useCompareAvailable(ctx)).result.current;

  it('is offered with the flag on, two models and the assistant, in agent, chat and web-search modes', () => {
    for (const mode of ['agent', 'chat', 'web-search'] as const) {
      setup({ mode });
      expect(available()).toBe(true);
    }
  });

  it.each([
    ['the flag is off', { flag: false }, ASSISTANT_CTX],
    ['only one model is configured', { models: [A] }, ASSISTANT_CTX],
    ['image mode is selected', { mode: 'image' as const }, ASSISTANT_CTX],
    ['the chat belongs to a saved agent', {}, 'agent-123'],
  ])('is not offered when %s', (_name, over, ctx) => {
    setup(over);
    expect(available(ctx)).toBe(false);
  });

  it('switching Compare on seeds the current model plus another, and is shown with the toggle', () => {
    setup();
    renderInTheme(<CompareToggle models={[llm(A), llm(B), llm(C)]} selectedModel={B} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(useCompareStore.getState().enabled).toBe(true);
    expect(useCompareStore.getState().models.map((m) => m.modelKey)).toEqual(['kb', 'ka']);
  });
});

describe('picking models', () => {
  it('never holds more than two, and deselecting frees a place', () => {
    const s = useCompareStore.getState();
    s.toggleModel(A);
    s.toggleModel(B);
    s.toggleModel(C);
    expect(useCompareStore.getState().models.map((m) => m.modelKey)).toEqual(['ka', 'kb']);
    s.toggleModel(A);
    s.toggleModel(C);
    expect(useCompareStore.getState().models.map((m) => m.modelKey)).toEqual(['kb', 'kc']);
    s.setModels([A, B, C]);
    expect(useCompareStore.getState().models).toHaveLength(2);
  });
});

describe('the compared turn', () => {
  it('draws the question once and two labelled columns', () => {
    const { primary, turn } = seed({ left: 'done', right: 'done' });
    renderColumns(primary, turn);
    expect(screen.getAllByTestId('question')).toHaveLength(1);
    expect(columns()).toHaveLength(2);
    expect(screen.getByRole('region', { name: 'Answer from Model A' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Answer from Model B' })).toBeTruthy();
    expect(screen.getByText('right answer')).toBeTruthy();
  });

  it('offers Use this answer on both columns once both are finished', () => {
    const { primary, turn } = seed({ left: 'done', right: 'done' });
    renderColumns(primary, turn);
    expect(useButtons().map((b) => b.getAttribute('aria-label'))).toEqual([
      'Use the answer from Model A',
      'Use the answer from Model B',
    ]);
  });

  it('offers it only on the finished column while the other streams, and gives the streaming one a Stop', () => {
    const { primary, branchSlotId, turn } = seed({ left: 'done', right: 'streaming' });
    renderColumns(primary, turn);
    expect(useButtons().map((b) => b.getAttribute('aria-label'))).toEqual(['Use the answer from Model A']);

    fireEvent.click(within(columns()[1]).getByRole('button', { name: 'Stop' }));
    expect(cancelStreamForSlot).toHaveBeenCalledWith(branchSlotId);
    expect(within(columns()[0]).queryByRole('button', { name: 'Stop' })).toBeNull();
  });

  it('gives the primary its own Stop', () => {
    const { primary, turn } = seed({ left: 'streaming', right: 'done' });
    renderColumns(primary, turn);
    fireEvent.click(within(columns()[0]).getByRole('button', { name: 'Stop' }));
    expect(cancelStreamForSlot).toHaveBeenCalledWith(primary);
  });

  it('shows a question card in the right column only, with no choose button there, while the left can be chosen', () => {
    const { primary, turn } = seed({ left: 'done', right: 'card' });
    renderColumns(primary, turn);
    expect(within(columns()[1]).getByText(/Which region for right\?/)).toBeTruthy();
    expect(within(columns()[0]).queryByText(/Which region/)).toBeNull();
    expect(useButtons().map((b) => b.getAttribute('aria-label'))).toEqual(['Use the answer from Model A']);
    expect(within(columns()[1]).getByText('Waiting')).toBeTruthy();
  });

  it('shows cards in both columns, each answerable on its own, with no choose button at all', () => {
    const { primary, turn } = seed({ left: 'card', right: 'card' });
    renderColumns(primary, turn);
    expect(within(columns()[0]).getByText(/Which region for left\?/)).toBeTruthy();
    expect(within(columns()[1]).getByText(/Which region for right\?/)).toBeTruthy();
    expect(useButtons()).toHaveLength(0);
  });

  it('answers the right column card in its own slot, with its own model', async () => {
    const { primary, branchSlotId, turn } = seed({ left: 'streaming', right: 'card' });
    renderColumns(primary, turn);
    const right = within(columns()[1]);
    fireEvent.click(right.getByText('US'));
    fireEvent.click(right.getByRole('button', { name: 'Submit' }));

    await waitFor(() => expect(streamMessageForSlot).toHaveBeenCalledTimes(1));
    const [slotId, , request] = vi.mocked(streamMessageForSlot).mock.calls[0];
    expect(slotId).toBe(branchSlotId);
    expect(request).toMatchObject({ modelKey: 'kb', conversationId: 'branch-1' });
    expect(useChatStore.getState().slots[branchSlotId!].pendingAskUserQuestion?.status).toBe('submitted');
    expect(useChatStore.getState().slots[primary].pendingAskUserQuestion).toBeNull();
  });

  it('does not offer a finished-looking column that failed, is empty or has not started', () => {
    for (const right of ['error', 'empty'] as const) {
      cleanup();
      useChatStore.setState({ slots: {}, activeSlotId: null });
      const { primary, turn } = seed({ left: 'done', right });
      renderColumns(primary, turn);
      expect(useButtons().map((b) => b.getAttribute('aria-label'))).toEqual(['Use the answer from Model A']);
    }
    cleanup();
    useChatStore.setState({ slots: {}, activeSlotId: null });
    const { primary, turn } = seed({ left: 'done', right: 'none', phase: 'starting' });
    renderColumns(primary, turn);
    expect(screen.getByText('Starting...')).toBeTruthy();
    expect(useButtons()).toHaveLength(1);
  });

  it('shows a calm line and a Retry for a model that failed; Retry makes a new branch', async () => {
    const local = fakeApi({
      'POST /api/v1/conversations/conv-1/compare/branch': { status: 200, data: { branchConversationId: 'branch-2' } },
    });
    const { primary, turn } = seed({ left: 'done', right: 'error' });
    renderColumns(primary, turn);
    const right = within(columns()[1]);
    expect(right.getByText('This model could not answer. Try again.')).toBeTruthy();
    expect(right.queryByText('boom')).toBeNull();

    fireEvent.click(right.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(local.count('POST /api/v1/conversations/conv-1/compare/branch')).toBe(1));
    await waitFor(() => expect(useCompareStore.getState().turns[primary]?.branchConvId).toBe('branch-2'));
  });

  it('has no Retry when the branch cannot be re-sent (restored after a reload)', () => {
    const { primary, turn } = seed({ left: 'done', right: 'error', template: false });
    renderColumns(primary, turn);
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('keeps both columns and says so when the server refuses to keep the answer yet', async () => {
    fakeApi({ 'POST /api/v1/conversations/conv-1/compare/choose': { status: 409, data: {} } });
    const { primary, turn } = seed({ left: 'done', right: 'done' });
    renderColumns(primary, turn);
    fireEvent.click(screen.getByRole('button', { name: 'Use the answer from Model B' }));

    expect(await screen.findByText(/not ready to keep yet/)).toBeTruthy();
    expect(columns()).toHaveLength(2);
    expect(useCompareStore.getState().turns[primary].chooseError).toBe('conflict');
  });

  it('keeps the primary when its Use this answer is pressed', async () => {
    const local = fakeApi({ 'POST /api/v1/conversations/conv-1/compare/choose': { status: 200, data: { ok: true } } });
    const { primary, turn } = seed({ left: 'done', right: 'done' });
    renderColumns(primary, turn);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Use the answer from Model A' }));
    });
    await waitFor(() => expect(useCompareStore.getState().turns[primary]).toBeUndefined());
    expect(local.sent[0].body).toEqual({ choice: 'primary' });
  });
});
