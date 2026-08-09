import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { RunResultCardPayload } from '../../../types';

/**
 * A workflow run used to be persisted as a bare `tool_call` and rendered by a
 * dedicated card, which meant its output arrived as literal markdown source
 * with no tabs, no citations and no copy/feedback actions. It is now an
 * ordinary `bot_response` carrying run metadata, so these assert the three
 * things that change buys: the run header replaces the (nonexistent) user
 * query, the answer goes through the normal markdown renderer, and the usual
 * message actions are present.
 */

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) => (typeof fallback === 'string' ? fallback : key),
    i18n: { language: 'en-US' },
  }),
  // `lib/i18n/config` runs at import time somewhere in this tree and needs a
  // plugin object to hand to `i18n.use()`.
  initReactI18next: { type: '3rdParty', init: () => undefined },
}));

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href }, children),
}));

vi.mock('@/lib/hooks/use-workflow-run-updates', () => ({
  useWorkflowRunUpdates: () => undefined,
}));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

vi.mock('@/lib/store/command-store', () => ({
  useCommandStore: () => undefined,
}));

vi.mock('../../../store', () => ({
  // Selector-style store: the component reads several slot-scoped slices
  // through it, all of which are empty for a plain persisted turn.
  useChatStore: (selector: (state: unknown) => unknown) =>
    selector({ activeSlotId: null, slots: {} }),
}));

vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: vi.fn() }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

vi.mock('../message-actions', () => ({
  MessageActions: () => React.createElement('div', { 'data-testid': 'message-actions' }),
}));

vi.mock('../response-tabs', () => ({
  ResponseTabs: () => React.createElement('div', { 'data-testid': 'response-tabs' }),
}));

vi.mock('../expandable-user-query', () => ({
  ExpandableUserQuery: ({ question }: { question: string }) =>
    React.createElement('div', { 'data-testid': 'user-query' }, question),
}));

const { ChatResponse } = await import('../chat-response');

const h = React.createElement;

afterEach(() => cleanup());

const RUN: RunResultCardPayload = {
  workflowId: 'wf-1',
  runId: 'run-1',
  status: 'succeeded',
  workflowName: 'Weekly digest',
  outputSummary: '# Digest\n\nThree tickets closed.',
  redirectLink: '/workflows?workflowId=wf-1',
} as RunResultCardPayload;

function renderResponse(props: Record<string, unknown>) {
  return render(
    h(Theme, null, h(ChatResponse, {
      question: '',
      answer: '',
      messageId: 'msg-1',
      ...props,
    } as never)),
  );
}

describe('ChatResponse with a workflow run', () => {
  it('renders the run header in place of the user query', () => {
    renderResponse({ workflowRun: RUN, answer: 'Three tickets closed.' });

    expect(screen.getByText('Weekly digest')).toBeTruthy();
    expect(screen.queryByTestId('user-query')).toBeNull();
  });

  it('renders the answer as markdown rather than literal source', () => {
    const { container } = renderResponse({
      workflowRun: RUN,
      answer: '# Digest\n\nThree tickets closed.',
    });

    expect(container.querySelector('h1')).toBeTruthy();
    expect(container.textContent).not.toContain('# Digest');
  });

  it('keeps the tabs and message actions a normal answer gets', () => {
    renderResponse({ workflowRun: RUN, answer: 'Three tickets closed.' });

    expect(screen.getByTestId('response-tabs')).toBeTruthy();
    expect(screen.getByTestId('message-actions')).toBeTruthy();
  });

  it('still renders the user query when there is no workflow run', () => {
    renderResponse({ question: 'What shipped?', answer: 'Three tickets closed.' });

    expect(screen.getByTestId('user-query')).toBeTruthy();
  });
});
