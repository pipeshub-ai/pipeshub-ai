import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({}) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../response-tabs', () => ({ ResponseTabs: () => <div /> }));
vi.mock('../confidence-indicator', () => ({ ConfidenceIndicator: () => <div /> }));
vi.mock('../answer-content', () => ({ AnswerContent: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock('../expandable-user-query', () => ({ ExpandableUserQuery: ({ question }: { question: string }) => <div>{question}</div> }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: {
    checkHandle: vi.fn().mockResolvedValue({ available: true }),
    createAgent: vi.fn(),
    getKnowledgeBasesForBuilder: vi.fn().mockResolvedValue({ knowledgeBases: [] }),
  },
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: { getAllMyToolsets: vi.fn().mockResolvedValue({ toolsets: [] }) } }));
vi.mock('../../../../knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useChatStore } from '../../../store';
import { AgentDraftPlaceholder } from '../agent-draft-placeholder';
import { ChatResponse } from '../chat-response';
import type { AgentDraft } from '../../../types';

const draft: AgentDraft = {
  draftId: 'd1',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Drafts offer letters',
  instructions: 'secret',
  knowledge: [],
  toolsets: [],
  suggestedTools: [],
  provenance: 'sender',
  requestedBy: 'u-a',
};

afterEach(() => {
  cleanup();
  useFeatureFlagsStore.setState({ flags: null });
});

describe('AgentDraftPlaceholder', () => {
  it('shows the requester the draft', () => {
    render(<Theme><AgentDraftPlaceholder draft={draft} /></Theme>);
    expect((screen.getByTestId('agent-draft-name') as HTMLInputElement).value).toBe('Offer drafter');
    expect((screen.getByTestId('agent-draft-handle') as HTMLInputElement).value).toBe('offer-drafter');
  });

  it('shows everyone else "X drafted an agent" and none of the contents', () => {
    const { container } = render(
      <Theme><AgentDraftPlaceholder draft={{ redacted: true, authorId: 'u-a' }} authorName="Alice" /></Theme>,
    );
    expect(screen.getByTestId('agent-draft-redacted').textContent).toBe('Alice drafted an agent');
    expect(container.textContent).not.toContain('Offer drafter');
  });

  it('falls back to "Someone" when the author is unknown', () => {
    render(<Theme><AgentDraftPlaceholder draft={{ redacted: true }} /></Theme>);
    expect(screen.getByTestId('agent-draft-redacted').textContent).toBe('Someone drafted an agent');
  });
});

describe('ChatResponse and the agent builder flag', () => {
  const row = (props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) =>
    render(
      <Theme>
        <ChatResponse
          question="make an agent" answer="Drafted." messageId="row-1" citationMessageRowKey="row-1"
          isLastMessage createdAt="2026-09-18T10:00:00.000Z" {...props}
        />
      </Theme>,
    );

  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_AGENT_BUILDER: true } });
  });

  it('renders the stored draft when the flag is on', () => {
    row({ persistedAgentDraft: draft });
    expect(screen.getByTestId('agent-draft-card')).toBeTruthy();
  });

  it('renders the redacted notice for a draft someone else asked for', () => {
    row({ persistedAgentDraft: { redacted: true, authorId: 'u-a' }, agentDraftAuthor: 'Alice' });
    expect(screen.getByTestId('agent-draft-redacted').textContent).toBe('Alice drafted an agent');
  });

  it('keeps a stored card but read-only when the flag is off', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_AGENT_BUILDER: false } });
    row({ persistedAgentDraft: draft });
    expect(screen.getByTestId('agent-draft-card').getAttribute('data-state')).toBe('off');
    expect(screen.getByText('Agent builder is turned off')).toBeTruthy();
    expect(screen.queryByTestId('agent-draft-create')).toBeNull();
  });

  it('shows no card with the flag off and nothing stored', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_CHAT_AGENT_BUILDER: false } });
    row({});
    expect(screen.queryByTestId('agent-draft-card')).toBeNull();
  });

  it('shows the live draft on the streaming row only', () => {
    const slotId = 'slot-1';
    useChatStore.setState({ activeSlotId: slotId, slots: { [slotId]: { liveAgentDraft: draft } } as never });
    row({ isStreaming: true });
    expect(screen.getByTestId('agent-draft-card')).toBeTruthy();
    cleanup();
    row({ isStreaming: false });
    expect(screen.queryByTestId('agent-draft-card')).toBeNull();
  });
});
