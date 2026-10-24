import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { UnavailableTool } from '../../../unavailable-tools';

const toolsetsApi = vi.hoisted(() => ({
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));
const mcpApi = vi.hoisted(() => ({ getMyMcpServers: vi.fn(), getOAuthAuthorizationUrl: vi.fn(), authenticate: vi.fn() }));
const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog', () => ({
  UserToolsetConfigDialog: ({ toolset, onSuccess }: { toolset: { displayName: string }; onSuccess: () => void }) => (
    <button type="button" onClick={onSuccess}>{`Save ${toolset.displayName}`}</button>
  ),
}));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../store', () => ({ useChatStore: { getState: () => ({ activeSlotId: 'slot-1' }) } }));

import { ConnectToolsCard } from '../connect-tools-card';
import { UnavailableToolsNotice } from '../unavailable-tools-notice';

const QUESTION = 'Summarise my unread email';

const GMAIL: UnavailableTool = {
  kind: 'toolset', instanceId: 'ts-gmail', displayName: 'Gmail', problem: 'not_authenticated',
  sharedCredential: false, userCanConnect: true,
};
const JIRA: UnavailableTool = {
  kind: 'toolset', instanceId: 'ts-jira', displayName: 'Jira', problem: 'not_configured',
  sharedCredential: false, userCanConnect: true,
};
const SEARCH: UnavailableTool = {
  kind: 'mcp', instanceId: 'mcp-search', displayName: 'Search', problem: 'not_authenticated', authMode: 'api_token',
  sharedCredential: true, userCanConnect: false,
};
const OLD: UnavailableTool = {
  kind: 'mcp', instanceId: 'mcp-old', displayName: 'Old Jira', problem: 'not_found', sharedCredential: false, userCanConnect: false,
};

function toolsetRow(overrides: Record<string, unknown> = {}) {
  return { name: 'gmail', displayName: 'Gmail', instanceId: 'ts-gmail', authType: 'OAUTH', isAuthenticated: false, ...overrides };
}

function fakePopup() {
  const popup = { closed: false, focus: vi.fn(), close: vi.fn() };
  popup.close.mockImplementation(() => {
    popup.closed = true;
  });
  return popup;
}

beforeEach(() => {
  installBrowserShims();
  toolsetsApi.getInstanceOAuthAuthorizationUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://accounts.example.com/o' });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('ConnectToolsCard', () => {
  it('lists toolsets and MCP servers with what each needs', () => {
    renderInTheme(
      <ConnectToolsCard items={[GMAIL, SEARCH, OLD]} serviceAccount={false} question={QUESTION} blocked={false} />,
    );

    expect(screen.getByText("This answer couldn't use some tools")).toBeTruthy();
    expect(screen.getByText('Gmail')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Connect' })).toHaveLength(1);
    expect(screen.getByText('Waiting for an admin to add the shared credential')).toBeTruthy();
    expect(screen.getByText("Removed. Ask the agent's owner to update it.")).toBeTruthy();
  });

  it('signs in to an OAuth toolset through the sign-in dialog, then asks the question again', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue(toolsetRow());
    renderInTheme(<ConnectToolsCard items={[GMAIL]} serviceAccount={false} question={QUESTION} blocked={false} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    expect(toolsetsApi.getInstanceOAuthAuthorizationUrl).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save Gmail' }));

    expect(screen.getByText('Connected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(chat.streamMessageForSlot).toHaveBeenCalledWith('slot-1', QUESTION, { slotId: 'slot-1', query: QUESTION });
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('opens the credentials dialog for a toolset that does not use OAuth', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue(toolsetRow({ instanceId: 'ts-jira', displayName: 'Jira', authType: 'API_TOKEN' }));
    renderInTheme(<ConnectToolsCard items={[JIRA]} serviceAccount={false} question={QUESTION} blocked />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Jira' }));

    expect(screen.getByText('Connected')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('says where to connect a toolset it cannot find', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue(undefined);
    renderInTheme(<ConnectToolsCard items={[GMAIL]} serviceAccount={false} question={QUESTION} blocked />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });

    expect(screen.getByRole('alert').textContent).toMatch(/Workspace → Actions/);
  });

  it('a stopped run offers no retry while something only an admin can fix is missing', async () => {
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue(toolsetRow({ instanceId: 'ts-jira', displayName: 'Jira', authType: 'API_TOKEN' }));
    renderInTheme(<ConnectToolsCard items={[JIRA, SEARCH]} serviceAccount={false} question={QUESTION} blocked />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Jira' }));

    expect(screen.getByText('Connected')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('a service account agent asks an admin and connects nothing here', () => {
    renderInTheme(
      <ConnectToolsCard
        items={[{ ...GMAIL, userCanConnect: false }]}
        agentId="sa-1"
        serviceAccount
        question={QUESTION}
        blocked
      />,
    );

    expect(screen.getByText("This agent's tools aren't connected")).toBeTruthy();
    expect(screen.getByText('Ask an admin to connect it')).toBeTruthy();
    expect(screen.getByText(/Ask an admin to connect Gmail in Agent Builder/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open Agent Builder' }).getAttribute('href')).toBe('/agents/edit?agentKey=sa-1');
  });
});

describe('UnavailableToolsNotice', () => {
  it('stays a chip under an answer that never needed the tools, and opens into the card', () => {
    renderInTheme(<UnavailableToolsNotice items={[GMAIL]} answer="I like coffee strong." question={QUESTION} />);

    expect(screen.queryByTestId('connect-tools')).toBeNull();
    fireEvent.click(screen.getByTestId('tools-unavailable-chip'));
    expect(screen.getByTestId('connect-tools')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('shows the card at once when the answer says it needed one', () => {
    renderInTheme(
      <UnavailableToolsNotice items={[GMAIL]} answer="I can't read your Gmail until you connect it." question={QUESTION} />,
    );

    expect(screen.queryByTestId('tools-unavailable-chip')).toBeNull();
    expect(screen.getByTestId('connect-tools')).toBeTruthy();
  });

  it('someone who did not ask reads which tools were missing and can connect nothing', () => {
    renderInTheme(
      <UnavailableToolsNotice
        items={[GMAIL, JIRA]}
        answer="I can't read your Gmail."
        question={QUESTION}
        readOnlyFor={{ agentName: 'Mail Helper' }}
      />,
    );

    expect(screen.getByTestId('tools-unavailable-line').textContent).toBe("Mail Helper couldn't use Gmail, Jira");
    expect(screen.queryByRole('button')).toBeNull();
  });
});
