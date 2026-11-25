/**
 * R49-PLAN (ADR-OWD-07 D-PLAN-1): a multi-step answer on the default chat shows its plan in the inline timeline, in
 * plain words. The worker journals the plan as `task.*` and hides the plan tool's own call; a chat saved before that
 * still names the call as a plain step, never by the tool's id.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { MessagePart, SSEPlanEvent } from '@/chat/types';

vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/api', async () => ({
  ...(await vi.importActual<object>('@/lib/api')),
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) },
}));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useActivityPanelStore } from '../../activity-panel/activity-panel-store';
import { describeTool } from '../../../utils/tool-display';

const initialChat = useChatStore.getState();
const RUN_ID = 'run-r49plan';

const STEPS: SSEPlanEvent['steps'] = [
  { taskId: 'task-1', title: 'Find last quarter’s invoices', status: 'completed', deps: [] },
  { taskId: 'task-2', title: 'Match them to the budget', status: 'in_progress', deps: [] },
  { taskId: 'task-3', title: 'Draft the summary', status: 'pending', deps: [] },
];

const WORK: MessagePart[] = [
  { type: 'text', content: 'I’ll reconcile the invoices step by step.', settled: true, runId: RUN_ID },
  { type: 'tool_call', toolCallId: 'c2', toolName: 'knowledgegraph__search', displayName: 'Searched the knowledge base', status: 'completed', runId: RUN_ID, durationMs: 1_200 },
];

function openDefaultChat() {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-default');
  // The default chat: no saved agent behind the conversation.
  store.updateSlot(slotId, { isInitialized: true, isOwner: true });
  store.setActiveSlot(slotId);
}

function renderAnswer(parts: MessagePart[]) {
  return render(
    <Theme>
      <ChatResponse
        question="Reconcile last quarter's invoices against the budget and draft a summary"
        answer="Here is the summary."
        messageId="m1"
        persistedParts={[...parts, { type: 'text', content: 'Here is the summary.', isFinal: true, runId: RUN_ID, elapsedMs: 41_000 }]}
        runId={RUN_ID}
      />
    </Theme>,
  );
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, focusSection: null, run: null });
});
afterEach(() => cleanup());

describe('R49-PLAN: the default chat shows its plan', () => {
  it('leads the inline timeline with the plan, each step in plain words', () => {
    openDefaultChat();
    renderAnswer([{ type: 'plan', runId: RUN_ID, steps: STEPS }, ...WORK]);
    const header = screen.getByRole('button', { name: /Completed 1 of 3 steps · Searched the knowledge base/ });
    fireEvent.click(header);
    const items = within(screen.getByRole('list', { name: 'Plan' })).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('Find last quarter’s invoices'),
      expect.stringContaining('Match them to the budget'),
      expect.stringContaining('Draft the summary'),
    ]);
    expect(items[0].textContent).toContain('Done');
    expect(items[1].textContent).toContain('In progress');
    expect(items[2].textContent).toContain('To do');
    expect(document.body.textContent).not.toMatch(/write_todos|todos/i);
  });

  it('a chat saved before the plan call was hidden names it as a plain step, never by its id', () => {
    openDefaultChat();
    renderAnswer([
      { type: 'plan', runId: RUN_ID, steps: STEPS },
      { type: 'tool_call', toolCallId: 'c1', toolName: 'write_todos', status: 'completed', runId: RUN_ID },
      ...WORK,
    ]);
    fireEvent.click(screen.getByRole('button', { name: /Completed 1 of 3 steps/ }));
    expect(screen.getAllByText(/Updated the plan/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/write_todos|Write todos/i);
  });

  it('every executor’s plan tool reads the same plain label', () => {
    for (const tool of ['write_todos', 'TodoWrite', 'todowrite', 'todo_write', 'update_plan']) {
      expect(describeTool(tool)).toMatchObject({ label: 'Updated the plan', present: 'Updating the plan', displayName: 'Updated the plan' });
    }
  });
});
