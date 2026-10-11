import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import type { McpConfigMissingDetails } from '../../../stream-error';

const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(),
  getOAuthAuthorizationUrl: vi.fn(),
  authenticate: vi.fn(),
}));
const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../store', () => ({ useChatStore: { getState: () => ({ activeSlotId: 'slot-1' }) } }));

import { McpConnectRequiredCard } from '../mcp-connect-required-card';

const QUESTION = 'Open issues assigned to me?';

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-gh', orgId: 'org-1', createdBy: 'u-1', name: 'GitHub', transport: 'streamable_http',
    authMode: 'oauth', useAdminAuth: false, requiredEnv: [], optionalEnv: [], isCustom: false,
    createdAt: 1, updatedAt: 1, isAuthenticated: false, tools: [], ...overrides,
  };
}

function details(overrides: Partial<McpConfigMissingDetails> = {}): McpConfigMissingDetails {
  return {
    agentId: 'agent-1',
    serviceAccount: false,
    servers: [{ instanceId: 'inst-gh', name: 'GitHub', problem: 'not_connected', authMode: 'oauth', sharedCredential: false }],
    ...overrides,
  };
}

function fakePopup() {
  const popup = { closed: false, location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() };
  popup.close.mockImplementation(() => {
    popup.closed = true;
  });
  return popup;
}

beforeEach(() => {
  installBrowserShims();
  mcpApi.getOAuthAuthorizationUrl.mockResolvedValue({ authorizationUrl: 'https://auth.example.com/authorize' });
  mcpApi.authenticate.mockResolvedValue({ success: true, isAuthenticated: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('McpConnectRequiredCard', () => {
  it('signs in to an OAuth server in a popup, then offers to send the question again', async () => {
    vi.useFakeTimers();
    const popup = fakePopup();
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [entry({ isAuthenticated: true })] });
    renderInTheme(<McpConnectRequiredCard details={details()} question={QUESTION} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    expect(open.mock.calls[0][0]).toBe('about:blank');
    expect(mcpApi.getOAuthAuthorizationUrl).toHaveBeenCalledWith('inst-gh', window.location.origin);
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();

    popup.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByText('Connected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', QUESTION);
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith('slot-1', QUESTION, { slotId: 'slot-1', query: QUESTION });
    // Sent once: the old reply keeps no second button.
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('says why when the sign-in was cancelled', async () => {
    vi.useFakeTimers();
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [entry()] });
    renderInTheme(<McpConnectRequiredCard details={details()} question={QUESTION} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    popup.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByRole('alert').textContent).toMatch(/cancel/i);
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('asks for a token in the dialog', async () => {
    const tokenServer = entry({ _id: 'inst-exa', name: 'Exa', authMode: 'api_token' });
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [tokenServer] });
    renderInTheme(
      <McpConnectRequiredCard
        details={details({
          servers: [{ instanceId: 'inst-exa', name: 'Exa', problem: 'not_connected', authMode: 'api_token', sharedCredential: false }],
        })}
        question={QUESTION}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText('Authenticate with Exa')).toBeTruthy();
    fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: 'exa_key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.getByText('Connected')).toBeTruthy());
    expect(mcpApi.authenticate).toHaveBeenCalledWith('inst-exa', { apiToken: 'exa_key' });
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('says so when the server cannot be found among mine', async () => {
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [] });
    renderInTheme(
      <McpConnectRequiredCard
        details={details({
          servers: [{ instanceId: 'inst-exa', name: 'Exa', problem: 'not_connected', authMode: 'api_token', sharedCredential: false }],
        })}
        question={QUESTION}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/Workspace → MCP Servers/);
  });

  it('offers nothing a member cannot do, and no retry that would fail again', () => {
    renderInTheme(
      <McpConnectRequiredCard
        details={details({
          servers: [
            { instanceId: 'inst-gone', name: 'Old Jira', problem: 'not_found', sharedCredential: false },
            { instanceId: 'inst-shared', name: 'Search', problem: 'not_connected', authMode: 'api_token', sharedCredential: true },
          ],
        })}
        question={QUESTION}
      />,
    );

    expect(screen.getByText("Removed. Ask the agent's owner to update it.")).toBeTruthy();
    expect(screen.getByText('Waiting for an admin to add the shared credential')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('sends a service account agent to Agent Builder', () => {
    renderInTheme(<McpConnectRequiredCard details={details({ serviceAccount: true })} question={QUESTION} />);

    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open Agent Builder' }).getAttribute('href')).toBe(
      '/agents/edit?agentKey=agent-1',
    );
  });
});
