import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import type { McpSignInServer } from '../../../types';

const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(),
  getAgentMcpServers: vi.fn(),
  getOAuthAuthorizationUrl: vi.fn(),
  getAgentOAuthAuthorizationUrl: vi.fn(),
}));
const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
}));
const store = vi.hoisted(() => ({
  state: {
    activeSlotId: 'slot-1' as string | null,
    slots: { 'slot-1': { isOwner: true } } as Record<string, { isOwner?: boolean }>,
    agentContextAccess: null as { agentKey: string; canEdit: boolean } | null,
  },
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../store', () => {
  const useChatStore = (selector: (s: typeof store.state) => unknown) => selector(store.state);
  useChatStore.getState = () => store.state;
  return { useChatStore };
});

import { McpSignInCard } from '../mcp-sign-in-card';

const DRIVE: McpSignInServer = { instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write'] };

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-drive', orgId: 'org-1', createdBy: 'u-1', name: 'Drive', transport: 'streamable_http',
    authMode: 'oauth', useAdminAuth: false, requiredEnv: [], optionalEnv: [], isCustom: false,
    createdAt: 1, updatedAt: 1, isAuthenticated: true, tools: [], connectedAt: 1000, ...overrides,
  };
}

function fakePopup() {
  const popup = { closed: false, location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() };
  popup.close.mockImplementation(() => {
    popup.closed = true;
  });
  return popup;
}

async function signInAndClosePopup(button: string) {
  vi.useFakeTimers();
  const popup = fakePopup();
  const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: button }));
  });
  popup.closed = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  return open;
}

beforeEach(() => {
  installBrowserShims();
  store.state.activeSlotId = 'slot-1';
  store.state.slots = { 'slot-1': { isOwner: true } };
  store.state.agentContextAccess = null;
  mcpApi.getOAuthAuthorizationUrl.mockResolvedValue({ authorizationUrl: 'https://auth.example.com/authorize' });
  mcpApi.getAgentOAuthAuthorizationUrl.mockResolvedValue({ authorizationUrl: 'https://auth.example.com/authorize' });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('McpSignInCard', () => {
  it('says what each server needs', () => {
    renderInTheme(<McpSignInCard servers={[DRIVE, { instanceId: 'inst-gh', serverName: 'GitHub', scopes: ['repo', 'workflow'] }]} />);
    expect(screen.getByText('Drive')).toBeTruthy();
    expect(screen.getByText('Needs: repo, workflow')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('signs in again in a popup, then sends a follow-up in agent mode', async () => {
    // Before the popup: the sign-in from earlier. After: a newer one.
    mcpApi.getMyMcpServers
      .mockResolvedValueOnce({ instances: [entry({ connectedAt: 1000 })] })
      .mockResolvedValue({ instances: [entry({ connectedAt: 2000 })] });
    renderInTheme(<McpSignInCard servers={[DRIVE]} />);

    const open = await signInAndClosePopup('Sign in again');

    expect(open.mock.calls[0][0]).toBe('about:blank');
    expect(mcpApi.getOAuthAuthorizationUrl).toHaveBeenCalledWith('inst-drive', window.location.origin);
    expect(mcpApi.getMyMcpServers).toHaveBeenCalledWith(false);
    expect(screen.getByText('Signed in')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    const text = 'I signed in to Drive again. Please try that again.';
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', text, undefined, undefined, { agentMode: true });
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith('slot-1', text, { slotId: 'slot-1', query: text });
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('a popup closed without a new sign-in is not one', async () => {
    // Already signed in before, and still the same sign-in after.
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [entry({ connectedAt: 1000 })] });
    renderInTheme(<McpSignInCard servers={[DRIVE]} />);

    await signInAndClosePopup('Sign in again');

    expect(screen.getByRole('alert').textContent).toMatch(/cancel/i);
    expect(screen.queryByText('Signed in')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it("doesn't open the provider when the current sign-in can't be read first", async () => {
    mcpApi.getMyMcpServers.mockRejectedValue(new Error('offline'));
    renderInTheme(<McpSignInCard servers={[DRIVE]} />);

    await signInAndClosePopup('Sign in again');

    expect(mcpApi.getOAuthAuthorizationUrl).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it("redoes an agent's own sign-in through the agent, for someone who can edit it", async () => {
    store.state.agentContextAccess = { agentKey: 'agent-7', canEdit: true };
    mcpApi.getAgentMcpServers
      .mockResolvedValueOnce({ instances: [entry({ connectedAt: 1000 })] })
      .mockResolvedValue({ instances: [entry({ connectedAt: 2000 })] });
    renderInTheme(<McpSignInCard servers={[{ ...DRIVE, agentKey: 'agent-7' }]} />);

    await signInAndClosePopup('Sign in again');

    expect(mcpApi.getAgentOAuthAuthorizationUrl).toHaveBeenCalledWith('agent-7', 'inst-drive');
    expect(mcpApi.getAgentMcpServers).toHaveBeenCalledWith('agent-7', false);
    expect(mcpApi.getOAuthAuthorizationUrl).not.toHaveBeenCalled();
    expect(screen.getByText('Signed in')).toBeTruthy();
  });

  it("points anyone else to an editor and the agent builder", () => {
    store.state.agentContextAccess = { agentKey: 'agent-7', canEdit: false };
    renderInTheme(<McpSignInCard servers={[{ ...DRIVE, agentKey: 'agent-7' }]} />);

    expect(screen.queryByRole('button', { name: 'Sign in again' })).toBeNull();
    expect(screen.getByText('Ask someone who can edit this agent to sign in again')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Agent Builder' }).getAttribute('href')).toBe('/agents/edit?agentKey=agent-7');
  });

  it("offers nothing to do in someone else's conversation", () => {
    store.state.slots = { 'slot-1': { isOwner: false } };
    renderInTheme(<McpSignInCard servers={[DRIVE]} />);
    expect(screen.queryByRole('button', { name: 'Sign in again' })).toBeNull();
  });
});
