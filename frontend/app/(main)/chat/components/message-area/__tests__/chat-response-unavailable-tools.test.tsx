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

const GMAIL: UnavailableTool = {
  kind: 'toolset', instanceId: 'ts-gmail', displayName: 'Gmail', problem: 'not_authenticated',
  sharedCredential: false, userCanConnect: true,
};

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
});

describe('ChatResponse for an answer that went on without some tools', () => {
  it('shows a quiet chip under an answer that never needed them', () => {
    renderInTheme(<ChatResponse question="Do you like coffee?" answer="I do." unavailableTools={[GMAIL]} />);

    expect(screen.getByText('I do.')).toBeTruthy();
    expect(screen.getByTestId('tools-unavailable-chip')).toBeTruthy();
    expect(screen.queryByTestId('connect-tools')).toBeNull();
  });

  it('shows the card when the answer says it needed one', () => {
    renderInTheme(
      <ChatResponse question="Unread mail?" answer="Gmail isn't connected, so I can't read it." unavailableTools={[GMAIL]} />,
    );

    expect(screen.getByTestId('connect-tools')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('shows nothing while the answer streams', () => {
    renderInTheme(<ChatResponse question="Q" answer="" isStreaming streamingContent="Gmail" unavailableTools={[GMAIL]} />);

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
