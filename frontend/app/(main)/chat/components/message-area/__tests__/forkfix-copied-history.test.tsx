/**
 * FORKFIX: opening a forked conversation. A manual test showed a fork of a durable agent chat asking
 * `…/runs/{source run}/timeline`, `…/followups` and `…/workspace/versions` under the fork, all 404. The server now
 * sends copied answers without the original's run ids and marked `copied`; the chat then asks no run route for them,
 * says once where the copied history ends, and treats the fork's own new turns as any durable chat's.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ConversationMessage } from '@/chat/types';

type ThreadMessage = { id: string; role: 'user' | 'assistant'; content: { type: 'text'; text: string }[] };

const thread = vi.hoisted(() => ({ messages: [] as ThreadMessage[], append: vi.fn() }));

vi.mock('@assistant-ui/react', () => ({
  useThread: () => ({ messages: thread.messages }),
  useThreadRuntime: () => ({ append: thread.append }),
  useAuiState: (select: (state: unknown) => unknown) => select({ thread: { messages: thread.messages } }),
  useAui: () => ({ thread: () => ({ append: thread.append }) }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock('@/chat/streaming', () => ({
  cancelStreamForSlot: vi.fn(),
  streamRegenerateForSlot: vi.fn(),
  loadOlderMessagesForSlot: vi.fn(async () => {}),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('@/chat/components/message-area/response-tabs/citations', async () => {
  const utils = await vi.importActual<typeof import('@/chat/components/message-area/response-tabs/citations/utils')>(
    '@/chat/components/message-area/response-tabs/citations/utils',
  );
  const control = await vi.importActual<
    typeof import('@/chat/components/message-area/response-tabs/citations/citation-popover-control')
  >('@/chat/components/message-area/response-tabs/citations/citation-popover-control');
  return {
    emptyCitationMaps: utils.emptyCitationMaps,
    buildCitationMapsFromApi: utils.buildCitationMapsFromApi,
    isCitationPopoverKeyStillValid: control.isCitationPopoverKeyStillValid,
    useCitationActions: () => ({}),
  };
});
vi.mock('@/chat/components/message-area/response-tabs/citations/inline-citation-popover-host', () => ({
  InlineCitationPopoverHost: () => null,
}));
vi.mock('@/chat/components/message-area/chat-response', () => ({
  ChatResponse: (props: { question: string; answer: string; runId?: string }) => (
    <article aria-label={props.question} data-run={props.runId ?? ''}>
      <p>{props.answer}</p>
    </article>
  ),
}));

import { MessageList } from '../message-list';
import { ChatApi } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { activityRun, messageRunId } from '@/chat/activity';
import { conversationHarness } from '@/chat/assistant-harness';
import { loadHistoricalMessages } from '@/chat/runtime';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { lastCopiedPairIndex } from '../copied-history-note';

const AGENT = 'b2b523bb-9a53-407e-a3f6-011ba237feb3';
const FORK = '6ad9f7501c469c87f85ad15b';
const SOURCE_RUNS = ['cd04a9d1-7652-4ad0-af2c-b3d7792a195a', 'ddec62f5-d4b8-4566-8c60-2c8f1e469be9'];
const OWN_RUN = 'own-run-in-the-fork';
const initialChat = useChatStore.getState();

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const getTimeline = vi.spyOn(ChatApi, 'getRunTimeline');
const getFollowups = vi.spyOn(ChatApi, 'getRunFollowups');
const listVersions = vi.spyOn(ChatApi, 'listWorkspaceVersions');

const message = (id: string, messageType: ConversationMessage['messageType'], content: string, extra: Partial<ConversationMessage> = {}) =>
  ({
    _id: id,
    messageType,
    content,
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: { chatMode: 'quick' },
    createdAt: '2026-10-22T11:45:21.221Z',
    updatedAt: '2026-10-22T11:45:21.221Z',
    feedback: [],
    ...extra,
  }) as ConversationMessage;

/** The fork as the conversation GET now serves it: copied answers keep their steps, name no run, and are marked. */
const forkAsServed = (): ConversationMessage[] => [
  message('m1', 'user_query', 'Write a python program that prints first ten prime numbers', { copied: true }),
  message('m2', 'bot_response', '2 3 5 7 11 13 17 19 23 29', {
    copied: true,
    parts: [
      { type: 'tool_call', toolName: 'code_task', status: 'completed' },
      { type: 'sub_agent', status: 'completed' },
      { type: 'text', text: 'Done', isFinal: true },
    ] as ConversationMessage['parts'],
  }),
  message('m3', 'user_query', 'And the next ten?', { copied: true }),
  message('m4', 'bot_response', '31 37 41 43 47 53 59 61 67 71', { copied: true, parts: [{ type: 'text', text: 'More' }] as ConversationMessage['parts'] }),
];

const ownTurn = (): ConversationMessage[] => [
  message('m5', 'user_query', 'Now in Rust'),
  message('m6', 'bot_response', 'fn main() {}', { runId: OWN_RUN }),
];

function openFork(api: ConversationMessage[]) {
  const { messages } = loadHistoricalMessages(api);
  thread.messages = messages as unknown as ThreadMessage[];
  const store = useChatStore.getState();
  const slotId = store.createSlot(FORK);
  store.updateSlot(slotId, { isInitialized: true, threadAgentId: AGENT, isOwner: true, durable: true, messages: messages as never });
  store.setActiveSlot(slotId);
  return useChatStore.getState().slots[slotId]!;
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
  getTimeline.mockReset();
  getTimeline.mockRejectedValue(new Error('404 Agent run not found'));
  getFollowups.mockReset();
  getFollowups.mockRejectedValue(new Error('404 Agent run not found'));
  listVersions.mockReset();
  listVersions.mockResolvedValue({ workspaceId: `ws:${FORK}`, head: 0, versions: [] });
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  HTMLElement.prototype.scrollTo = function scrollTo() {} as HTMLElement['scrollTo'];
});

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
});

describe('FORKFIX: a copied answer as the chat maps it', () => {
  it('keeps its saved steps and marks it copied, with no run to read', () => {
    const { messages } = loadHistoricalMessages(forkAsServed());
    const answer = messages[1] as { metadata: { custom: Record<string, unknown> } };
    expect(answer.metadata.custom['copied']).toBe(true);
    expect(answer.metadata.custom['durableRunId']).toBeUndefined();
    expect((answer.metadata.custom['persistedParts'] as unknown[]).length).toBe(3);
  });

  it('names no run for the activity panel or the chips, and the fork stays on its durable executor', () => {
    const slot = openFork(forkAsServed());
    expect(activityRun(slot)).toBeNull();
    expect(conversationHarness(slot)).toBe('durable');
  });

  it("names the fork's own run once a new turn is answered in it", () => {
    const slot = openFork([...forkAsServed(), ...ownTurn()]);
    expect(activityRun(slot)).toEqual({ agentId: AGENT, sessionId: FORK, runId: OWN_RUN });
  });

  it('never names a run for a copied answer, even one that still carries a run id or an engine task run', () => {
    const custom = {
      copied: true,
      durableRunId: SOURCE_RUNS[1],
      persistedParts: [{ type: 'engine_task', runId: SOURCE_RUNS[0], taskId: SOURCE_RUNS[0] }],
    };
    expect(messageRunId(custom)).toBeNull();
    expect(messageRunId({ ...custom, copied: undefined })).toBe(SOURCE_RUNS[1]);
  });

  it('finds where the copied history ends', () => {
    expect(lastCopiedPairIndex([])).toBe(-1);
    expect(lastCopiedPairIndex([{ copied: true }, { copied: true }, {}])).toBe(1);
    expect(lastCopiedPairIndex([{}, {}])).toBe(-1);
  });
});

describe('FORKFIX: opening a forked conversation', () => {
  it('regression (fork of a durable chat): asks no timeline or follow-ups for a copied answer; the workspace list is the fork\'s own and answers', async () => {
    openFork(forkAsServed());
    render(
      <Theme>
        <MessageList />
      </Theme>,
    );
    expect(await screen.findByText('31 37 41 43 47 53 59 61 67 71')).toBeTruthy();
    await waitFor(() => expect(listVersions).toHaveBeenCalledWith({ agentId: AGENT, sessionId: FORK }, { limit: 50 }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(getTimeline).not.toHaveBeenCalled();
    expect(getFollowups).not.toHaveBeenCalled();
    for (const call of listVersions.mock.calls) expect(call[0]).toEqual({ agentId: AGENT, sessionId: FORK });
    for (const article of screen.getAllByRole('article')) expect(article.getAttribute('data-run')).toBe('');
  });

  it('says once, under the last copied answer, that its detailed activity stays with the original', async () => {
    openFork(forkAsServed());
    render(
      <Theme>
        <MessageList />
      </Theme>,
    );
    const notes = await screen.findAllByTestId('copied-history-note');
    expect(notes).toHaveLength(1);
    expect(notes[0]!.textContent).toContain('Messages above were copied from the original conversation. Their detailed activity stays there.');
    const last = screen.getByRole('article', { name: 'And the next ten?' });
    expect(last.compareDocumentPosition(notes[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a new turn in the fork reads its own run, under the fork, and the note stays above it", async () => {
    getTimeline.mockResolvedValue({
      runId: OWN_RUN, parentRunId: null, status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null, followups: [], tasks: [],
    });
    getFollowups.mockResolvedValue([]);
    openFork([...forkAsServed(), ...ownTurn()]);
    render(
      <Theme>
        <MessageList />
      </Theme>,
    );
    await waitFor(() => expect(getFollowups).toHaveBeenCalled());
    for (const [run] of [...getTimeline.mock.calls, ...getFollowups.mock.calls]) {
      expect(run).toMatchObject({ sessionId: FORK, runId: OWN_RUN });
    }
    const note = screen.getByTestId('copied-history-note');
    const own = screen.getByRole('article', { name: 'Now in Rust' });
    expect(note.compareDocumentPosition(own) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(own.getAttribute('data-run')).toBe(OWN_RUN);
    for (const runId of SOURCE_RUNS) expect(JSON.stringify(getTimeline.mock.calls)).not.toContain(runId);
  });

  it('a conversation nobody forked shows no note', async () => {
    const { messages } = loadHistoricalMessages(ownTurn());
    thread.messages = messages as unknown as ThreadMessage[];
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-plain');
    store.updateSlot(slotId, { isInitialized: true, isOwner: true, durable: true, messages: messages as never });
    store.setActiveSlot(slotId);
    getTimeline.mockResolvedValue({
      runId: OWN_RUN, parentRunId: null, status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null, followups: [], tasks: [],
    });
    getFollowups.mockResolvedValue([]);
    render(
      <Theme>
        <MessageList />
      </Theme>,
    );
    expect(await screen.findByText('fn main() {}')).toBeTruthy();
    expect(screen.queryByTestId('copied-history-note')).toBeNull();
  });
});
