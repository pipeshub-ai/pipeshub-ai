/**
 * UX5 FU-6 (and FU-7's chips on screen): suggested next steps under the
 * latest completed answer. The questions come from the run's follow-ups
 * route after the run ended (waiting for them when this tab saw it finish),
 * and from its timeline. A chip fills the real composer and focuses it; it
 * never sends. The message list and the composer render together, as on the
 * page; only their heavy neighbours are stubbed.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { RunTimelineView, SSEEngineProgressEvent } from '@/chat/types';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '../../../__tests__/token-vector';

type ThreadMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: { type: 'text'; text: string }[];
};

const thread = vi.hoisted(() => ({ messages: [] as ThreadMessage[], append: vi.fn() }));
const streaming = vi.hoisted(() => ({
  cancelStreamForSlot: vi.fn(),
  streamRegenerateForSlot: vi.fn(),
  loadOlderMessagesForSlot: vi.fn(async () => {}),
}));

vi.mock('@assistant-ui/react', () => ({
  useThread: () => ({ messages: thread.messages }),
  useThreadRuntime: () => ({ append: thread.append }),
  useAuiState: (select: (state: unknown) => unknown) => select({ thread: { messages: thread.messages } }),
  useAui: () => ({ thread: () => ({ append: thread.append }) }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock('@/chat/streaming', () => streaming);
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/hooks/use-chat-speech-recognition', () => ({
  useChatSpeechRecognition: () => ({
    isListening: false,
    isSupported: false,
    transcript: '',
    interimTranscript: '',
    unavailableReason: null,
    toggle: vi.fn(),
    stop: vi.fn(),
    resetTranscript: vi.fn(),
  }),
}));
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
    isCitationPopoverKeyStillValid: control.isCitationPopoverKeyStillValid,
    useCitationActions: () => ({}),
  };
});
vi.mock('@/chat/components/message-area/response-tabs/citations/inline-citation-popover-host', () => ({
  InlineCitationPopoverHost: () => null,
}));
vi.mock('@/chat/components/message-area/chat-response', () => ({
  ChatResponse: (props: { question: string; answer: string; isStreaming: boolean }) => (
    <article aria-label={props.question}>
      <p>{props.answer}</p>
    </article>
  ),
}));
vi.mock('@/chat/components/chat-panel', async () => ({
  ...(await vi.importActual<object>('@/chat/components/chat-panel/plus-menu-button')),
  ...(await vi.importActual<object>('@/chat/components/chat-panel/agent-strategy-mode-switcher')),
  ...(await vi.importActual<object>('@/chat/components/chat-panel/expansion-panels/agent-strategy-mode-panel')),
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel', () => ({
  ChatInputOverlayPanel: () => null,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel', () => ({
  getAppliedReasoningEffortLabel: () => '',
  ModelSelectorPanel: () => null,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/connectors-collections/connectors-collections-panel', () => ({
  ConnectorsCollectionsPanel: () => null,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/agent-scoped-resources-panel', () => ({
  AgentScopedResourcesPanel: () => null,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/universal-agent-resources-panel', () => ({
  UniversalAgentResourcesPanel: () => null,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/mobile-query-options-sheet', () => ({
  MobileQueryOptionsSheet: () => null,
}));

import { MessageList } from '../message-list';
import { ChatInput } from '../../chat-input';
import { ChatApi } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useActivityPanelStore } from '../../activity-panel/activity-panel-store';

const RUN = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const initialChat = useChatStore.getState();
const FOLLOWUPS = ['Which customers are affected?', 'Draft a reply to finance', 'Compare with last quarter'];

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const getTimeline = vi.spyOn(ChatApi, 'getRunTimeline');
const getFollowups = vi.spyOn(ChatApi, 'getRunFollowups');

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function timeline(patch: Partial<RunTimelineView> = {}): RunTimelineView {
  return {
    runId: RUN.runId,
    parentRunId: null,
    status: 'completed',
    parts: [],
    engine: null,
    approvals: [],
    children: [],
    merge: null,
    budget: null,
    followups: FOLLOWUPS.map((text) => ({ text })),
    ...patch,
  };
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, focusSection: null });
  thread.messages = [
    { id: 'u1', role: 'user', content: [{ type: 'text', text: 'Why did refunds rise?' }] },
    { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'Three late shipments.' }] },
    { id: 'u2', role: 'user', content: [{ type: 'text', text: 'Which regions?' }] },
    { id: 'a2', role: 'assistant', content: [{ type: 'text', text: 'Mostly EMEA.' }] },
  ];
  thread.append.mockReset();
  streaming.cancelStreamForSlot.mockReset();
  streaming.streamRegenerateForSlot.mockReset();
  getTimeline.mockReset();
  getTimeline.mockResolvedValue(timeline());
  getFollowups.mockReset();
  getFollowups.mockResolvedValue([]);
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  HTMLElement.prototype.scrollTo = function scrollTo() {} as HTMLElement['scrollTo'];
});

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  // The list scrolls in an animation frame: let a pending one run before the stub goes.
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
});

function openConversation(patch: Record<string, unknown> = {}) {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, threadAgentId: 'agent-1', lastDurableRun: RUN, ...patch });
  store.setActiveSlot(slotId);
  return slotId;
}

function renderChat() {
  const onSend = vi.fn();
  render(
    <Theme>
      <MessageList />
      <ChatInput onSend={onSend} />
    </Theme>,
  );
  return { onSend };
}

/** A chip's label, without its aria-hidden icon. */
const chipLabels = () => screen.getAllByTestId('followup-chip').map((chip) => chip.lastChild?.textContent);

const composer = () => screen.getByPlaceholderText('Ask anything...') as HTMLTextAreaElement;

describe('FU-6: suggested next steps', () => {
  it('shows the run\'s 2–3 suggestions under the latest completed answer only', async () => {
    openConversation();
    renderChat();
    const group = await screen.findByRole('group', { name: 'Suggested next steps' });
    expect(within(group).getAllByRole('button')).toHaveLength(3);
    for (const text of FOLLOWUPS) expect(within(group).getByRole('button', { name: text })).toBeTruthy();
    expect(getTimeline).toHaveBeenCalledWith(RUN);
    expect(getFollowups).toHaveBeenCalledWith(RUN, 12_000, expect.any(AbortSignal));
    expect(screen.getAllByTestId('followup-chips')).toHaveLength(1);

    const [, latest] = screen.getAllByRole('article');
    const chips = screen.getByTestId('followup-chips');
    expect(latest!.compareDocumentPosition(chips) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(latest!.parentElement!.contains(chips)).toBe(true);
  });

  it('a click fills the composer and focuses it; nothing is sent', async () => {
    openConversation();
    const { onSend } = renderChat();
    const chip = await screen.findByRole('button', { name: 'Draft a reply to finance' });
    expect(chip.getAttribute('aria-describedby')).toBeTruthy();
    fireEvent.click(chip);
    await waitFor(() => expect(document.activeElement).toBe(composer()));
    expect(composer().value).toBe('Draft a reply to finance');
    expect(onSend).not.toHaveBeenCalled();
    expect(thread.append).not.toHaveBeenCalled();
    expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();
  });

  it('none while a run streams, and the timeline is not read until it ends', async () => {
    openConversation({ isStreaming: true, durableRun: RUN });
    renderChat();
    await Promise.resolve();
    expect(screen.queryByTestId('followup-chips')).toBeNull();
    expect(getTimeline).not.toHaveBeenCalled();
    expect(getFollowups).not.toHaveBeenCalled();
  });

  it('CTL-5: shown whatever an older Labs page stored for the retired switch', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_FOLLOWUP_SUGGESTIONS: false, ENABLE_AGENT_ACTIVITY_PANEL: false } });
    openConversation();
    renderChat();
    expect(await screen.findByTestId('followup-chips')).toBeTruthy();
  });

  it('none for a timeline of another run', async () => {
    getTimeline.mockResolvedValue(timeline({ runId: 'run-other' }));
    openConversation();
    renderChat();
    await waitFor(() => expect(getTimeline).toHaveBeenCalled());
    expect(screen.queryByTestId('followup-chips')).toBeNull();
  });
});

describe('FU: follow-ups that arrive after the run ended', () => {
  it('the answer completes first; the chips appear once the route answers, without a reload', async () => {
    getTimeline.mockResolvedValue(timeline({ followups: [] }));
    const offered = deferred<{ text: string }[]>();
    getFollowups.mockReturnValue(offered.promise);
    const slotId = openConversation({ isStreaming: true, durableRun: RUN });
    renderChat();
    expect(getFollowups).not.toHaveBeenCalled();

    act(() => useChatStore.getState().updateSlot(slotId, { isStreaming: false, durableRun: null }));
    await waitFor(() => expect(getFollowups).toHaveBeenCalledWith(RUN, 12_000, expect.any(AbortSignal)));
    expect(screen.getByText('Mostly EMEA.')).toBeTruthy();
    expect(screen.queryByTestId('followup-chips')).toBeNull();

    await act(async () => offered.resolve([{ text: 'Which customers are affected?' }]));
    const group = await screen.findByRole('group', { name: 'Suggested next steps' });
    expect(within(group).getByRole('button', { name: 'Which customers are affected?' })).toBeTruthy();
    expect(chipLabels()).toEqual(['Which customers are affected?']);
    expect(getFollowups).toHaveBeenCalledTimes(1);
  });

  it('keeps the timeline\'s too, once each', async () => {
    getTimeline.mockResolvedValue(timeline({ followups: [{ text: 'Draft a reply to finance' }, { text: 'Compare with last quarter' }] }));
    getFollowups.mockResolvedValue([{ text: 'Draft a reply to finance' }]);
    openConversation();
    renderChat();
    await waitFor(() => expect(screen.getAllByTestId('followup-chip')).toHaveLength(2));
    expect(chipLabels()).toEqual(['Draft a reply to finance', 'Compare with last quarter']);
  });

  it('a failed call shows no chips and changes nothing else', async () => {
    getTimeline.mockResolvedValue(timeline({ followups: [] }));
    const offered = deferred<{ text: string }[]>();
    getFollowups.mockReturnValue(offered.promise);
    openConversation();
    renderChat();
    await waitFor(() => expect(getFollowups).toHaveBeenCalled());
    await act(async () => offered.reject(new Error('503')));
    await waitFor(() => expect(getTimeline).toHaveBeenCalled());
    expect(screen.queryByTestId('followup-chips')).toBeNull();
    expect(screen.getByText('Mostly EMEA.')).toBeTruthy();
    expect(composer().value).toBe('');
  });

  it('after a reload (a run this tab did not follow) it asks without waiting', async () => {
    openConversation({
      lastDurableRun: null,
      isOwner: true,
      messages: [
        { id: 'u2', role: 'user', content: [{ type: 'text', text: 'Which regions?' }] },
        { id: 'a2', role: 'assistant', content: [{ type: 'text', text: 'Mostly EMEA.' }], metadata: { custom: { durableRunId: RUN.runId } } },
      ],
    });
    renderChat();
    await waitFor(() => expect(getFollowups).toHaveBeenCalledWith(RUN, 0, expect.any(AbortSignal)));
  });
});

describe('FU-7 on screen: an engine run\'s action chips', () => {
  const frame = (kind: string, text: string): SSEEngineProgressEvent => ({ tool: 'code_task', taskId: 'run-1', kind, text });

  it('"Run the tests again" fills the composer; "Open the changed files" opens the panel on Files', async () => {
    getTimeline.mockResolvedValue(
      timeline({
        followups: [],
        engine: { taskId: 'run-1', truncated: false, frames: [frame('command', 'pytest -q tests'), frame('diff', 'src/rounding.py')] },
      }),
    );
    openConversation();
    const { onSend } = renderChat();
    fireEvent.click(await screen.findByRole('button', { name: 'Run the tests again' }));
    await waitFor(() => expect(composer().value).toBe('Run the tests again: pytest -q tests'));
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Open the changed files' }));
    expect(useActivityPanelStore.getState()).toMatchObject({ open: true, focusSection: 'files' });
  });
});

describe('QA-14 QA14-F1: a suggestion carrying a credential', () => {
  for (const example of VECTOR_EXAMPLES) {
    it(`${example.id}: the chip and the composer it fills show the mask`, async () => {
      getTimeline.mockResolvedValue(timeline({ followups: [{ text: `Rotate ${example.secret} now?` }] }));
      getFollowups.mockResolvedValue([{ text: `Revoke ${example.secret}?` }]);
      openConversation();
      renderChat();
      const group = await screen.findByRole('group', { name: 'Suggested next steps' });
      await waitFor(() => expect(within(group).getAllByRole('button')).toHaveLength(2));
      expect(chipLabels()).toEqual([`Revoke ${VECTOR_MASK}?`, `Rotate ${VECTOR_MASK} now?`]);
      fireEvent.click(within(group).getByRole('button', { name: `Revoke ${VECTOR_MASK}?` }));
      await waitFor(() => expect(composer().value).toBe(`Revoke ${VECTOR_MASK}?`));
      expect(document.body.textContent).not.toContain(example.secret);
    });
  }
});

