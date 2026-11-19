import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';

const renderRow = (props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) =>
  renderInTheme(
    <ChatResponse question="Find my issues" answer="Two open issues." messageId="msg-1" isLastMessage {...props} />,
  );

beforeEach(() => installBrowserShims());
afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('ChatResponse search-degraded note', () => {
  it('shows the note below the answer text when the search was partial', () => {
    renderRow({ searchDegraded: true });

    const note = screen.getByText('Results may be incomplete.');
    const answer = screen.getByText('Two open issues.');
    expect(Boolean(answer.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it('shows no note for a complete search, or while the answer is still streaming', () => {
    renderRow();
    expect(screen.queryByText('Results may be incomplete.')).toBeNull();
    cleanup();

    renderRow({ searchDegraded: true, isStreaming: true });
    expect(screen.queryByText('Results may be incomplete.')).toBeNull();
  });
});

describe('ChatResponse access notices', () => {
  const notices = [{ code: 'connector_syncing' as const, connectorId: 'c1', connectorName: 'Jira' }];

  it('says a source is still syncing, below the answer', () => {
    renderRow({ accessNotices: notices });

    const note = screen.getByText('Jira is still syncing, so results from it may be incomplete.');
    expect(Boolean(screen.getByText('Two open issues.').compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it('shows nothing without notices or while the answer is still streaming', () => {
    renderRow();
    expect(screen.queryByTestId('access-notices')).toBeNull();
    cleanup();

    renderRow({ accessNotices: notices, isStreaming: true });
    expect(screen.queryByTestId('access-notices')).toBeNull();
  });
});
