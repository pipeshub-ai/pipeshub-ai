import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';

const MESSAGE = "This agent requires the following MCP servers to be set up — not authenticated: 'GitHub'.";
const STREAM_ERROR = {
  code: 'mcp_server_config_missing',
  details: {
    agentId: 'agent-1',
    serviceAccount: false,
    servers: [{ instanceId: 'inst-gh', name: 'GitHub', problem: 'not_connected', authMode: 'oauth' }],
  },
};

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('ChatResponse for a run stopped on MCP servers', () => {
  it('shows the Connect card instead of the error text', () => {
    renderInTheme(<ChatResponse question="Q" answer={MESSAGE} streamError={STREAM_ERROR} />);

    expect(screen.getByTestId('mcp-connect-required')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });

  it('keeps the text for any other error', () => {
    renderInTheme(
      <ChatResponse question="Q" answer="The model is not responding." streamError={{ code: 'stream_error' }} />,
    );

    expect(screen.queryByTestId('mcp-connect-required')).toBeNull();
    expect(screen.getByText('The model is not responding.')).toBeTruthy();
  });
});

describe('ChatResponse retry on a failed answer', () => {
  it('hands back the whole turn, attachments and filters included', async () => {
    const onRetry = vi.fn();
    const attachments = [
      { recordId: 'r1', recordName: 'plan.pdf', mimeType: 'application/pdf', extension: 'pdf', virtualRecordId: 'v1' },
    ];
    const appliedFilters = { apps: [{ id: 'app-1', name: 'Drive', nodeType: '', connector: '' }], kb: [] };
    renderInTheme(
      <ChatResponse
        question=""
        answer="The model is not responding."
        failed
        isLastMessage
        attachments={attachments}
        appliedFilters={appliedFilters}
        onRetry={onRetry}
      />,
    );

    screen.getByTestId('answer-failed-retry').click();

    expect(onRetry).toHaveBeenCalledWith({ question: '', attachments, collections: undefined, appliedFilters });
  });
});

describe('ChatResponse regenerate', () => {
  it('offers Regenerate only to someone who can send in this chat', () => {
    const { unmount } = renderInTheme(<ChatResponse question="Q" answer="An answer." isLastMessage messageId="m1" />);
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
    unmount();

    renderInTheme(<ChatResponse question="Q" answer="An answer." isLastMessage messageId="m1" canSend={false} />);
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull();
  });
});

describe('ChatResponse for a stopped run', () => {
  it('marks the words it kept as stopped, and says so when it stopped before any answer', () => {
    renderInTheme(<ChatResponse question="Q" answer="The first part." status="stopped" />);
    expect(screen.getByTestId('chat-stopped-marker').textContent).toBe('Stopped');
    cleanup();

    renderInTheme(<ChatResponse question="Q" answer="" status="stopped" />);
    expect(screen.getByTestId('chat-stopped-marker').textContent).toBe('Stopped before an answer began');
  });
});
