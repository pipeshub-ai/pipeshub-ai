import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { UnavailableTool } from '../../../unavailable-tools';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { UnavailableToolsNotice } from '../unavailable-tools-notice';

const GMAIL: UnavailableTool = {
  kind: 'toolset', instanceId: 'ts-gmail', displayName: 'Gmail', problem: 'not_authenticated',
  sharedCredential: false, userCanConnect: true,
};
/** As the AI service saves a tool whose stub the answer called. */
const NEEDED_GMAIL: UnavailableTool = { ...GMAIL, needed: true };

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('ChatResponse for an answer that went on without some tools', () => {
  // `unavailableTools` on an answer are the tools whose stub its run called: the ones it needed.
  it('says nothing under an answer that called no tool it lacks, even one that names a tool', () => {
    renderInTheme(<ChatResponse question="Do you like coffee?" answer="I do, and so does Gmail." />);

    expect(screen.getByText('I do, and so does Gmail.')).toBeTruthy();
    expect(screen.queryByTestId('connect-tools')).toBeNull();
    expect(screen.queryByText(/connected/)).toBeNull();
  });

  it('shows the card for the tools the answer called, whatever its words call them', () => {
    renderInTheme(
      <ChatResponse question="Unread mail?" answer="Your mail isn't connected, so I can't read it." unavailableTools={[NEEDED_GMAIL]} />,
    );

    expect(screen.getByTestId('connect-tools')).toBeTruthy();
    expect(screen.getByText('Gmail')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('lists exactly those tools, never one the answer only names', () => {
    renderInTheme(
      <ChatResponse question="Issues?" answer="Jira isn't connected, and Gmail isn't either." unavailableTools={[NEEDED_GMAIL]} />,
    );

    expect(screen.getByText('Gmail')).toBeTruthy();
    expect(screen.queryByText('Jira')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Connect' })).toHaveLength(1);
  });

  it('says nothing under an answer saved before the stubs, whose list named every tool left out', () => {
    renderInTheme(
      <ChatResponse question="Unread mail?" answer="Gmail isn't connected, so I can't read it." unavailableTools={[GMAIL]} />,
    );

    expect(screen.queryByTestId('connect-tools')).toBeNull();
  });

  it('tells nobody but the asker, even when the answer says it needed one', () => {
    renderInTheme(
      <UnavailableToolsNotice
        items={[NEEDED_GMAIL]}
        question="Unread mail?"
        readOnlyFor={{ agentName: 'Mail Helper' }}
      />,
    );

    expect(screen.queryByTestId('tools-unavailable-line')).toBeNull();
    expect(screen.queryByTestId('connect-tools')).toBeNull();
    expect(screen.queryByText(/Gmail/)).toBeNull();
  });

  it('shows nothing while the answer streams', () => {
    renderInTheme(<ChatResponse question="Q" answer="" isStreaming streamingContent="Gmail" unavailableTools={[NEEDED_GMAIL]} />);

    expect(screen.queryByTestId('tools-unavailable-chip')).toBeNull();
    expect(screen.queryByTestId('connect-tools')).toBeNull();
  });
});

describe('ChatResponse for a service account agent stopped on its tools', () => {
  it('shows the card with the items instead of the error text', () => {
    const message = 'This agent uses its own connections, and these aren’t set up: Gmail.';
    renderInTheme(
      <ChatResponse
        question="Q"
        answer={message}
        failed
        streamError={{
          code: 'toolset_config_missing',
          details: { agentId: 'sa-1', serviceAccount: true, items: [{ ...GMAIL, userCanConnect: false }], servers: [] },
        }}
      />,
    );

    expect(screen.getByTestId('connect-tools')).toBeTruthy();
    expect(screen.getByText('Ask an admin to connect it')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.queryByText(message)).toBeNull();
  });
});
