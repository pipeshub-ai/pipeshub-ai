import React from 'react';
import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AssistantMessage } from '../assistant-message';
import type { MessagePart } from '../../../types';
// Imported from the concrete module (not the `response-tabs/citations`
// barrel) — the barrel also re-exports `ReferenceCard`, whose transitive
// `@/knowledge-base/api` import isn't needed here since SourcesTab /
// CitationsTab are mocked below.
import { emptyCitationMaps } from '../response-tabs/citations/utils';

// `assistant-message.tsx` itself imports from the `response-tabs/citations`
// barrel (for `emptyCitationMaps`/types), which transitively pulls in
// `ReferenceCard` -> `@/knowledge-base/api` -> the shared axios instance,
// hydrating the auth store from `window.localStorage` at module load.
// jsdom's storage stubbing is unreliable for that eager side effect, so
// replace the API module entirely.
vi.mock('@/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    streamRecord: vi.fn(),
    getRecordDetails: vi.fn(),
  },
}));

// `assistant-message.tsx`'s full dependency graph (via `message-actions.tsx`
// -> `../../api` -> `@/lib/api`) reaches `lib/api/axios-instance.ts`, which
// calls `hydrateAuthStore()` at module load time — before `beforeAll` runs.
// That reads `window.localStorage`, which Node's `--localstorage-file` CLI
// flag (set in this repo's test runner env) replaces with a broken
// experimental global. Mock `auth-store` directly at the source rather than
// polyfilling `localStorage` — this is the same fix already used by
// `lib/api/__tests__/streaming.test.ts` for the identical root cause.
vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ accessToken: 'test-access-token' }) },
  hydrateAuthStore: vi.fn(),
  logoutAndRedirect: vi.fn(),
}));

beforeAll(() => {
  window.matchMedia = window.matchMedia ?? ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

/**
 * `AssistantMessage` is a container: it decides WHICH child renders and with
 * what suppression rules, but delegates actual rendering to child
 * components that have their own dedicated tests (`agent-activity`,
 * `response-tabs`, etc.) or are straightforward presentational
 * components. Mocking every child here keeps this suite a true unit test of
 * `AssistantMessage`'s own branching logic (streaming vs. final, ask-user-
 * question suppression, etc.) instead of re-testing children's internals —
 * and sidesteps their heavier transitive dependencies (SSE streaming, the
 * chat store, markdown rendering) that aren't relevant to this component.
 */
vi.mock('../confidence-indicator', () => ({
  ConfidenceIndicator: ({ confidence }: { confidence: string }) => (
    <div data-testid="confidence">{confidence}</div>
  ),
}));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => (
    <div data-testid="answer-content">{content}</div>
  ),
}));
vi.mock('../status-message', () => ({
  StatusMessageComponent: ({ status }: { status: { message: string } }) => (
    <div data-testid="status-message">{status.message}</div>
  ),
}));
vi.mock('../message-actions', () => ({
  MessageActions: ({ isLastMessage, messageId }: { isLastMessage?: boolean; messageId?: string }) => (
    <div data-testid="message-actions" data-is-last={String(!!isLastMessage)} data-message-id={messageId ?? ''} />
  ),
}));
vi.mock('../response-tabs', () => ({
  ResponseTabs: ({ activeTab }: { activeTab: string }) => (
    <div data-testid="response-tabs" data-active-tab={activeTab} />
  ),
}));
vi.mock('../response-tabs/citations/sources-tab', () => ({
  SourcesTab: () => <div data-testid="sources-tab" />,
}));
vi.mock('../response-tabs/citations/citations-tab', () => ({
  CitationsTab: () => <div data-testid="citations-tab" />,
}));
vi.mock('../artifacts-panel', () => ({
  ArtifactsPanel: () => <div data-testid="artifacts-panel" />,
}));
vi.mock('../ask-user-question-card', () => ({
  AskUserQuestionCard: ({ status }: { status: string }) => (
    <div data-testid="ask-user-question-card">{status}</div>
  ),
}));
vi.mock('../agent-activity', () => ({
  AgentActivityTimeline: () => <div data-testid="agent-activity" />,
}));
vi.mock('../download-tasks', () => ({
  DownloadTasks: () => <div data-testid="download-tasks" />,
}));
// NOTE: these three resolve one level deeper than the component-level mocks
// above — `assistant-message.tsx` lives in `message-area/` and reaches
// `chat/{streaming,runtime,store}.ts` via `../../*`, but this test file lives
// one directory further down at `message-area/__tests__/`, so the same
// modules are `../../../*` from here.
vi.mock('../../../streaming', () => ({
  streamMessageForSlot: vi.fn(),
}));
vi.mock('../../../runtime', () => ({
  buildStreamChatRequestForSlot: vi.fn(),
}));

// Minimal store double — AssistantMessage only reads `setPreviewFile` and the
// active slot's `pendingAskUserQuestion` at render time.
let mockPendingAskUserQuestion: unknown = null;
const mockSetPreviewFile = vi.fn();
vi.mock('../../../store', () => ({
  useChatStore: Object.assign(
    (selector: (s: Record<string, unknown>) => unknown) =>
      selector({
        activeSlotId: 'slot-1',
        slots: { 'slot-1': { pendingAskUserQuestion: mockPendingAskUserQuestion } },
        setPreviewFile: mockSetPreviewFile,
      }),
    {
      getState: () => ({
        activeSlotId: 'slot-1',
        slots: { 'slot-1': { pendingAskUserQuestion: mockPendingAskUserQuestion } },
        previewFile: null,
        setPreviewFile: mockSetPreviewFile,
        updateSlot: vi.fn(),
      }),
    },
  ),
}));

afterEach(() => {
  cleanup();
  mockPendingAskUserQuestion = null;
  vi.clearAllMocks();
});

const h = React.createElement;
const EMPTY_CITATION_MAPS = emptyCitationMaps();

function renderAssistantMessage(props: Partial<React.ComponentProps<typeof AssistantMessage>> = {}) {
  return render(
    h(
      Theme,
      null,
      h(AssistantMessage, {
        question: 'What is the refund policy?',
        answer: 'Refunds are processed within 5 business days.',
        citationMaps: EMPTY_CITATION_MAPS,
        ...props,
      }),
    ),
  );
}

describe('AssistantMessage — answer rendering', () => {
  it('renders the final answer via AnswerContent when not streaming', () => {
    renderAssistantMessage({ answer: 'Final answer text.' });
    expect(screen.getByTestId('answer-content').textContent).toBe('Final answer text.');
  });

  it('renders streaming content instead of the (empty) final answer while streaming', () => {
    renderAssistantMessage({
      answer: '',
      isStreaming: true,
      streamingContent: 'Partial answer so far',
    });
    expect(screen.getByTestId('answer-content').textContent).toBe('Partial answer so far');
  });

  it('shows a fallback "thinking" status while streaming with no content yet', () => {
    renderAssistantMessage({ answer: '', isStreaming: true, streamingContent: '' });
    expect(screen.getByTestId('status-message')).toBeTruthy();
    expect(screen.queryByTestId('answer-content')).toBeNull();
  });

  it('prefers the explicit currentStatusMessage over the fallback while streaming', () => {
    renderAssistantMessage({
      answer: '',
      isStreaming: true,
      streamingContent: '',
      currentStatusMessage: { id: 's-1', status: 'executing', message: 'Searching knowledge base…', timestamp: '' },
    });
    expect(screen.getByTestId('status-message').textContent).toBe('Searching knowledge base…');
  });

  it('never shows the streaming status once the message has finished streaming', () => {
    renderAssistantMessage({ answer: 'Done.', isStreaming: false });
    expect(screen.queryByTestId('status-message')).toBeNull();
  });
});

describe('AssistantMessage — confidence indicator', () => {
  it('shows confidence only when not streaming', () => {
    renderAssistantMessage({ confidence: 'High', isStreaming: false });
    expect(screen.getByTestId('confidence').textContent).toBe('High');
  });

  it('hides confidence while streaming even if a value is present', () => {
    renderAssistantMessage({ confidence: 'High', isStreaming: true, streamingContent: 'partial' });
    expect(screen.queryByTestId('confidence')).toBeNull();
  });
});

describe('AssistantMessage — agent activity timeline', () => {
  it('renders the timeline when persisted parts exist', () => {
    const persistedParts: MessagePart[] = [{ type: 'tool_call', toolName: 'web_search', status: 'completed' }];
    renderAssistantMessage({ persistedParts, isStreaming: false });
    expect(screen.getByTestId('agent-activity')).toBeTruthy();
  });

  it('does not render the timeline when there are no parts', () => {
    renderAssistantMessage({ persistedParts: undefined, streamingParts: undefined });
    expect(screen.queryByTestId('agent-activity')).toBeNull();
  });
});

describe('AssistantMessage — ask_user_question suppression', () => {
  it('renders a persisted ask_user_question card instead of the answer/sources', () => {
    renderAssistantMessage({
      answer: 'This should be hidden.',
      persistedAskUserQuestion: { name: 'ask_user_question', questions: [{ uuid: 'q-1', question: 'Pick one', options: [{ id: 'o-1', label: 'A' }] }] },
    });

    expect(screen.getByTestId('ask-user-question-card').textContent).toBe('persisted');
    expect(screen.queryByTestId('answer-content')).toBeNull();
  });

  it('renders the active pending question card when it matches this row, hiding the answer', () => {
    mockPendingAskUserQuestion = {
      assistantMessageId: 'row-1',
      payload: { name: 'ask_user_question', questions: [{ uuid: 'q-1', question: 'Pick one', options: [{ id: 'o-1', label: 'A' }] }] },
      answers: {},
      status: 'pending',
    };
    renderAssistantMessage({
      answer: 'This should be hidden too.',
      citationMessageRowKey: 'row-1',
    });

    expect(screen.getByTestId('ask-user-question-card').textContent).toBe('pending');
    expect(screen.queryByTestId('answer-content')).toBeNull();
  });

  it('renders the answer normally when a pending question exists for a different row', () => {
    mockPendingAskUserQuestion = {
      assistantMessageId: 'row-other',
      payload: { name: 'ask_user_question', questions: [] },
      answers: {},
      status: 'pending',
    };
    renderAssistantMessage({ answer: 'Visible answer.', citationMessageRowKey: 'row-1' });

    expect(screen.getByTestId('answer-content').textContent).toBe('Visible answer.');
    expect(screen.queryByTestId('ask-user-question-card')).toBeNull();
  });
});

describe('AssistantMessage — message actions', () => {
  it('forwards isLastMessage and messageId to MessageActions', () => {
    renderAssistantMessage({ isLastMessage: true, messageId: 'msg-42' });
    const actions = screen.getByTestId('message-actions');
    expect(actions.getAttribute('data-is-last')).toBe('true');
    expect(actions.getAttribute('data-message-id')).toBe('msg-42');
  });

  it('always renders MessageActions, even while streaming', () => {
    renderAssistantMessage({ isStreaming: true, streamingContent: 'partial' });
    expect(screen.getByTestId('message-actions')).toBeTruthy();
  });
});
