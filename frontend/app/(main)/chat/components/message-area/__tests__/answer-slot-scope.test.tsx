/**
 * Answer actions act on the answer's own slot. Without an `AnswerSlotContext` provider that is the
 * active slot, as always; with one (an answer drawn beside the active chat), tool approvals, MCP
 * "Try again", question-card answers, feedback and Regenerate all go to the provided slot and never
 * to the active one.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, renderHook, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import type { AskUserQuestionPayload, PendingAskUserQuestion, ToolApprovalDetails } from '../../../types';

type FakeSlot = {
  convId: string;
  threadAgentId: string | null;
  isOwner: boolean;
  isStreaming: boolean;
  pendingAskUserQuestion: PendingAskUserQuestion | null;
  activeExpandedMessageId: string | null;
};
type FakeState = {
  activeSlotId: string | null;
  agentContextAccess: null;
  previewFile: null;
  slots: Record<string, FakeSlot>;
  updateSlot(slotId: string, patch: Partial<FakeSlot>): void;
  setPreviewFile(): void;
  setPreviewMode(): void;
};

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  submitFeedback: vi.fn(async () => {}),
  submitAgentFeedback: vi.fn(async () => {}),
}));
const store = vi.hoisted(() => ({ state: null as unknown as FakeState }));
const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(),
  getAgentMcpServers: vi.fn(),
  getOAuthAuthorizationUrl: vi.fn(),
  getAgentOAuthAuthorizationUrl: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span>{name}</span> }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({ useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }) }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../api', () => ({
  ChatApi: { submitFeedback: chat.submitFeedback, submitAgentFeedback: chat.submitAgentFeedback },
}));
vi.mock('../../../store', () => {
  const useChatStore = (selector: (state: FakeState) => unknown) => selector(store.state);
  useChatStore.getState = () => store.state;
  return { useChatStore };
});
// The card itself is tested on its own; here it only hands back answers.
vi.mock('../ask-user-question-card', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../ask-user-question-card')>()),
  AskUserQuestionCard: ({
    onAnswersChange,
    onSubmit,
  }: {
    onAnswersChange?: (answers: Record<string, unknown>) => void;
    onSubmit?: (message: string, answers: Record<string, unknown>) => void;
  }) => (
    <>
      <button type="button" onClick={() => onAnswersChange?.({ q1: 'EU' })}>Pick EU</button>
      <button type="button" onClick={() => onSubmit?.('User selections: EU', { q1: 'EU' })}>Send answers</button>
    </>
  ),
}));

import { AnswerSlotContext, answerSlotIdNow, useAnswerSlotId } from '../answer-slot-scope';
import { ToolApprovalCard } from '../tool-approval-card';
import { McpConnectRequiredCard } from '../mcp-connect-required-card';
import { McpSignInCard } from '../mcp-sign-in-card';
import { MessageActions } from '../message-actions';
import { ChatResponse } from '../chat-response';
import { useCommandStore } from '@/lib/store/command-store';

const ACTIVE = 'slot-active';
const SIDE = 'slot-side';

function slot(convId: string): FakeSlot {
  return {
    convId,
    threadAgentId: null,
    isOwner: true,
    isStreaming: false,
    pendingAskUserQuestion: null,
    activeExpandedMessageId: null,
  };
}

function freshState(): FakeState {
  const state: FakeState = {
    activeSlotId: ACTIVE,
    agentContextAccess: null,
    previewFile: null,
    slots: { [ACTIVE]: slot('conv-active'), [SIDE]: slot('conv-side') },
    updateSlot: (slotId, patch) => {
      state.slots[slotId] = { ...state.slots[slotId], ...patch };
    },
    setPreviewFile: () => {},
    setPreviewMode: () => {},
  };
  return state;
}

function inSide(ui: React.ReactElement) {
  return renderInTheme(<AnswerSlotContext.Provider value={SIDE}>{ui}</AnswerSlotContext.Provider>);
}

const APPROVAL: ToolApprovalDetails = {
  approvalId: 'ap-1',
  instanceId: 'inst-1',
  serverName: 'Jira',
  toolName: 'create_issue',
  toolTitle: 'Create an issue',
  expiresAt: Date.now() + 15 * 60_000,
  arguments: { title: 'Login fails' },
};

function serverEntry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-gh', orgId: 'org-1', createdBy: 'u-1', name: 'GitHub', transport: 'streamable_http',
    authMode: 'oauth', useAdminAuth: false, requiredEnv: [], optionalEnv: [], isCustom: false,
    createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [], ...overrides,
  };
}

/** Clicks `button`, then lets the sign-in popup close so the card counts the server as signed in. */
async function signInThroughPopup(button: string) {
  vi.useFakeTimers();
  const popup = { closed: false, location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: button }));
  });
  popup.closed = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
}

beforeEach(() => {
  installBrowserShims();
  store.state = freshState();
  mcpApi.getOAuthAuthorizationUrl.mockResolvedValue({ authorizationUrl: 'https://auth.example.com/authorize' });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('the answer slot without a provider', () => {
  it('is the active slot, at render and when a handler runs', () => {
    const { result } = renderHook(() => useAnswerSlotId());
    expect(result.current).toBe(ACTIVE);
    expect(answerSlotIdNow(null)).toBe(ACTIVE);

    store.state.activeSlotId = SIDE;
    expect(answerSlotIdNow(null)).toBe(SIDE);
  });

  it('keeps answer actions on the active slot', () => {
    renderInTheme(<ToolApprovalCard approval={APPROVAL} isLatest rowId="row-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));

    expect(chat.streamMessageForSlot).toHaveBeenCalledWith(
      ACTIVE, 'Tool approval: allow', { slotId: ACTIVE, query: 'Tool approval: allow' }, { reuseRowId: 'row-1' },
    );
  });
});

describe('the answer slot inside AnswerSlotContext', () => {
  it('is the provided slot, whatever is active', () => {
    const { result } = renderHook(() => useAnswerSlotId(), {
      wrapper: ({ children }) => <AnswerSlotContext.Provider value={SIDE}>{children}</AnswerSlotContext.Provider>,
    });
    expect(result.current).toBe(SIDE);
    expect(answerSlotIdNow(SIDE)).toBe(SIDE);
  });

  it('answers a tool approval on the provided slot', () => {
    inSide(<ToolApprovalCard approval={APPROVAL} isLatest rowId="row-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));

    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith(SIDE, 'Tool approval: allow', undefined, {
      approvalId: 'ap-1',
      decision: 'allow',
    });
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith(
      SIDE, 'Tool approval: allow', { slotId: SIDE, query: 'Tool approval: allow' }, { reuseRowId: 'row-1' },
    );
  });

  it('asks the provided slot who can answer a tool approval', () => {
    store.state.slots[SIDE].isOwner = false;
    inSide(<ToolApprovalCard approval={APPROVAL} isLatest />);

    expect(screen.queryByRole('button', { name: 'Allow' })).toBeNull();
  });

  it('sends the question again on the provided slot after an MCP server is connected', async () => {
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [serverEntry()] });
    inSide(
      <McpConnectRequiredCard
        question="Open issues assigned to me?"
        details={{
          agentId: 'agent-1',
          serviceAccount: false,
          servers: [{ instanceId: 'inst-gh', name: 'GitHub', problem: 'not_connected', authMode: 'oauth', sharedCredential: false }],
        }}
      />,
    );

    await signInThroughPopup('Connect');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(chat.streamMessageForSlot).toHaveBeenCalledWith(SIDE, 'Open issues assigned to me?', {
      slotId: SIDE,
      query: 'Open issues assigned to me?',
    });
  });

  it('sends the follow-up on the provided slot after signing in to an MCP server again', async () => {
    mcpApi.getMyMcpServers
      .mockResolvedValueOnce({ instances: [serverEntry({ _id: 'inst-drive', name: 'Drive', connectedAt: 1000 })] })
      .mockResolvedValue({ instances: [serverEntry({ _id: 'inst-drive', name: 'Drive', connectedAt: 2000 })] });
    inSide(<McpSignInCard servers={[{ instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write'] }]} />);

    await signInThroughPopup('Sign in again');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(chat.streamMessageForSlot.mock.calls[0][0]).toBe(SIDE);
  });

  it('keeps a question card and its answers on the provided slot', () => {
    const payload = { questions: [{ question: 'Which region?', options: [] }] } as unknown as AskUserQuestionPayload;
    const card: PendingAskUserQuestion = { assistantMessageId: 'a1', payload, answers: {}, status: 'pending' };
    store.state.slots[SIDE].pendingAskUserQuestion = card;
    inSide(<ChatResponse question="Plan the launch" answer="" messageId="a1" citationMessageRowKey="a1" isLastMessage />);

    fireEvent.click(screen.getByRole('button', { name: 'Pick EU' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send answers' }));

    expect(store.state.slots[SIDE].pendingAskUserQuestion).toMatchObject({ status: 'submitted', answers: { q1: 'EU' } });
    expect(store.state.slots[ACTIVE].pendingAskUserQuestion).toBeNull();
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith(
      SIDE, 'User selections: EU', { slotId: SIDE, query: 'User selections: EU' }, { resumeAskUserQuestion: true },
    );
  });

  it("sends feedback to the provided slot's conversation", async () => {
    inSide(<MessageActions content="An answer." messageId="a1" question="Q" isLastMessage />);

    fireEvent.click(screen.getByRole('button', { name: 'Helpful' }));
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Excellent answer' }));
    });

    expect(chat.submitFeedback).toHaveBeenCalledWith('conv-side', 'a1', { isHelpful: true, categories: ['excellent_answer'] });
  });

  it('names the provided slot when it asks to regenerate, and no slot without a provider', () => {
    const regenerate = vi.fn();
    useCommandStore.getState().register('showRegenBar', regenerate);

    inSide(<MessageActions content="An answer." messageId="a1" question="Q" isLastMessage />);
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    cleanup();
    renderInTheme(<MessageActions content="An answer." messageId="a1" question="Q" isLastMessage />);
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    expect(regenerate.mock.calls[0][0]).toEqual({ messageId: 'a1', text: 'Q', appliedFilters: undefined, slotId: SIDE });
    expect(regenerate.mock.calls[1][0]).toEqual({ messageId: 'a1', text: 'Q', appliedFilters: undefined });
    useCommandStore.getState().unregister('showRegenBar');
  });
});
