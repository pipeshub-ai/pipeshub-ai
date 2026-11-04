/**
 * The sign-in popups (toolsets, connectors, MCP servers) stay open across a
 * sign-out in another tab. The answer to their code exchange is then dropped,
 * so each one reports the failure instead of spinning forever with the opener
 * never told.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const exchange = vi.hoisted(() => vi.fn());
const posts = vi.hoisted(() => ({
  toolsetError: vi.fn(),
  connectorError: vi.fn(),
  mcpError: vi.fn(),
}));

vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: { completeToolsetOAuthCallback: exchange } }));
vi.mock('@/app/(main)/workspace/connectors/api', () => ({ ConnectorsApi: { completeConnectorOAuthCallback: exchange } }));
vi.mock('@/app/(main)/workspace/connectors/components/authenticate-tab/use-connector-oauth-popup', () => ({
  OAUTH_CALLBACK_SUCCESS_DISPLAY_MS: 900,
}));
vi.mock('@/app/(main)/toolsets/oauth/toolset-oauth-window-messages', () => ({
  postToolsetOAuthErrorToOpener: posts.toolsetError,
  postToolsetOAuthSuccessToOpener: vi.fn(),
}));
vi.mock('@/app/(main)/connectors/oauth/connector-oauth-window-messages', () => ({
  postConnectorOAuthErrorToOpener: posts.connectorError,
  postConnectorOAuthSuccessToOpener: vi.fn(),
}));
vi.mock('@/app/(main)/workspace/mcp-servers/oauth/mcp-oauth-window-messages', () => ({
  postMcpOAuthErrorToOpener: posts.mcpError,
  postMcpOAuthSuccessToOpener: vi.fn(),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { clearSessionCache } from '@/lib/api/session-cache';
import i18n from '@/lib/i18n/config';
import { ToolsetOAuthCallbackClient } from '../toolsets/oauth/callback/toolset-oauth-callback-client';
import { ConnectorOAuthCallbackClient } from '../connectors/oauth/callback/connector-oauth-callback-client';
import { McpOAuthCallbackClient } from '../mcp-servers/oauth/callback/mcp-oauth-callback-client';

const popups = [
  {
    name: 'toolset',
    path: '/toolsets/oauth/callback/slack',
    Popup: () => <ToolsetOAuthCallbackClient />,
    finished: { success: true },
    told: (hint: string) => expect(posts.toolsetError).toHaveBeenCalledWith('slack', hint),
    post: posts.toolsetError,
    hint: () => i18n.t('agentBuilder.toolsetOAuthErrorRetryHint'),
  },
  {
    name: 'connector',
    path: '/connectors/oauth/callback/slack',
    Popup: () => <ConnectorOAuthCallbackClient />,
    finished: { redirectUrl: '/workspace/connectors/c1' },
    told: (hint: string) => expect(posts.connectorError).toHaveBeenCalledWith(hint),
    post: posts.connectorError,
    hint: () => 'OAuth authentication failed.',
  },
  {
    name: 'MCP server',
    path: '/mcp-servers/oauth/callback',
    Popup: () => <McpOAuthCallbackClient completeCallback={exchange} />,
    finished: { success: true, instanceId: 'i1' },
    told: (hint: string) => expect(posts.mcpError).toHaveBeenCalledWith(hint),
    post: posts.mcpError,
    hint: () => i18n.t('workspace.mcpServers.oauth.retryHint'),
  },
];

async function open(Popup: () => React.ReactElement) {
  render(
    <Theme>
      <Popup />
    </Theme>,
  );
  await act(async () => {});
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe.each(popups)('the $name sign-in popup at session end', ({ path, Popup, finished, told, post, hint }) => {
  beforeEach(() => {
    window.history.replaceState(null, '', `${path}?code=c1&state=s1`);
    exchange.mockReturnValue(new Promise(() => {}));
  });

  it('shows the error and tells the opener instead of spinning forever', async () => {
    await open(Popup);
    expect(exchange).toHaveBeenCalledTimes(1);

    await act(async () => clearSessionCache());

    told(hint());
    expect(screen.getByText(hint())).toBeTruthy();
  });

  it('reports once, and not again at a later session end', async () => {
    await open(Popup);

    await act(async () => clearSessionCache());
    await act(async () => clearSessionCache());

    expect(post).toHaveBeenCalledTimes(1);
  });

  it('is not affected by a session end after the exchange finished', async () => {
    exchange.mockResolvedValue(finished);
    await open(Popup);

    await act(async () => clearSessionCache());

    expect(post).not.toHaveBeenCalled();
  });
});
