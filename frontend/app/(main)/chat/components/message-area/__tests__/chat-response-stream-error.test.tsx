import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

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
