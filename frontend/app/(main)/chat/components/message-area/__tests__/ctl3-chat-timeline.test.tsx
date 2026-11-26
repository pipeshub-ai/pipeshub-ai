/**
 * CTL-3: an answer shows its work the way Claude does. While the run works the steps are open, with the plan on
 * top; when it ends they fold into one summary line. "View activity" opens the drawer for that answer's run, and
 * only on click.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart, SSEPlanEvent } from '@/chat/types';

// MessageActions forks through the app router (main #4225); these tests render no router.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
// The answer's neighbours (model label, approvals) ask the server; nothing here depends on their answers.
vi.mock('@/lib/api', async () => ({
  ...(await vi.importActual<object>('@/lib/api')),
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) },
}));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useActivityPanelStore } from '../../activity-panel/activity-panel-store';

const initialChat = useChatStore.getState();
const RUN_ID = 'run-ctl3';

const PARTS: MessagePart[] = [
  { type: 'text', content: 'I’ll look up the travel policy.', settled: true, runId: RUN_ID },
  { type: 'tool_call', toolCallId: 'c1', toolName: 'knowledgegraph__search_entities', displayName: 'Searched the knowledge base', status: 'completed', runId: RUN_ID, durationMs: 1_600 },
  { type: 'tool_call', toolCallId: 'c2', toolName: 'knowledgegraph__fetch_record', displayName: 'Read the full record', status: 'completed', runId: RUN_ID, durationMs: 500 },
  { type: 'text', content: 'Book flights 14 days ahead.', isFinal: true, runId: RUN_ID, elapsedMs: 53_400 },
];

const PLAN: SSEPlanEvent = {
  runId: RUN_ID,
  currentTaskId: null,
  steps: [
    { taskId: 't1', title: 'Find the travel policy', status: 'completed', deps: [] },
    { taskId: 't2', title: 'Summarise the rules', status: 'completed', deps: ['t1'] },
  ],
};

function openConversation(patch: Record<string, unknown> = {}) {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, threadAgentId: 'agent-1', isOwner: true, ...patch });
  store.setActiveSlot(slotId);
}

function renderAnswer(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return render(
    <Theme>
      <ChatResponse
        question="What is our travel policy?"
        answer="Book flights 14 days ahead."
        messageId="m1"
        persistedParts={PARTS}
        runId={RUN_ID}
        {...props}
      />
    </Theme>,
  );
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, focusSection: null, run: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('CTL-3: a finished answer', () => {
  it('folds its steps into one summary line, opened on click', () => {
    openConversation();
    renderAnswer();
    const header = screen.getByRole('button', { name: /Searched the knowledge base · Read the full record · 53s/ });
    expect(header.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(header);
    expect(header.getAttribute('aria-expanded')).toBe('true');
  });

  it('leads with its plan when this tab saw the run plan', () => {
    openConversation({ plan: PLAN });
    renderAnswer();
    const header = screen.getByRole('button', { name: /Completed 2 of 2 steps · Searched the knowledge base · Read the full record · 53s/ });
    fireEvent.click(header);
    expect(within(screen.getByRole('list', { name: 'Plan' })).getAllByRole('listitem')).toHaveLength(2);
  });

  it('CTL-3b: keeps its plan after a reload, from the saved plan part', () => {
    openConversation();
    renderAnswer({ persistedParts: [{ type: 'plan', runId: RUN_ID, steps: PLAN.steps }, ...PARTS] });
    const header = screen.getByRole('button', { name: /Completed 2 of 2 steps/ });
    fireEvent.click(header);
    expect(within(screen.getByRole('list', { name: 'Plan' })).getAllByRole('listitem')).toHaveLength(2);
  });

  it('never shows another run\'s plan', () => {
    openConversation({ plan: { ...PLAN, runId: 'run-other' } });
    renderAnswer();
    expect(screen.queryByRole('button', { name: /Completed/ })).toBeNull();
  });

  it('"View activity" opens the drawer for this answer\'s run, and nothing opens before the click', () => {
    openConversation();
    renderAnswer();
    expect(useActivityPanelStore.getState().open).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'View activity' }));
    expect(useActivityPanelStore.getState()).toMatchObject({
      open: true,
      run: { agentId: 'agent-1', sessionId: 'conv-1', runId: RUN_ID },
    });
  });

  it('offers no "View activity" in someone else\'s conversation, or on an answer with no run', () => {
    openConversation({ isOwner: false });
    const { unmount } = renderAnswer();
    expect(screen.queryByRole('button', { name: 'View activity' })).toBeNull();
    unmount();
    useChatStore.setState(initialChat, true);
    openConversation();
    renderAnswer({ runId: undefined });
    expect(screen.queryByRole('button', { name: 'View activity' })).toBeNull();
  });
});

describe('CTL-3: an answer being written', () => {
  it('shows the steps open, under a live summary that counts the time since the question, with the plan on top', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    openConversation({ plan: { ...PLAN, steps: [PLAN.steps[0], { ...PLAN.steps[1], status: 'in_progress' }] } });
    vi.setSystemTime(new Date('2026-10-15T12:00:30.000Z'));
    renderAnswer({
      isStreaming: true,
      isLastMessage: true,
      createdAt: '2026-10-15T12:00:00.000Z',
      persistedParts: undefined,
      streamingParts: PARTS.slice(0, 2),
      streamingContent: '',
    });
    const header = screen.getByRole('button', { name: /Completed 1 of 2 steps · Searched the knowledge base · 30s/ });
    expect(header.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('list', { name: 'Plan' })).toBeTruthy();
  });
});
