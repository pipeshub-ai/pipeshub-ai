import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

const api = vi.hoisted(() => ({
  getMyToolsets: vi.fn(),
  getAllMyToolsets: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api, MAX_TOOLSETS_LIST_LIMIT: 200 }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({
  McpServersApi: { getMyMcpServers: vi.fn(async () => ({ instances: [] })) },
}));
vi.mock('../connectors-collections/collections-tab', () => ({ CollectionsTab: () => null }));
// The real dialog posts the credentials; here "Save" stands for credentials that were accepted.
vi.mock('@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog', () => ({
  UserToolsetConfigDialog: ({ toolset, onSuccess }: { toolset: BuilderSidebarToolset; onSuccess: () => void }) => (
    <div role="dialog" aria-label={`Sign in to ${toolset.instanceName}`}>
      <button type="button" onClick={onSuccess}>
        Save
      </button>
    </div>
  ),
}));

import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { UniversalAgentResourcesPanel } from '../universal-agent-resources-panel';

function toolset(overrides: Partial<BuilderSidebarToolset>): BuilderSidebarToolset {
  return {
    name: 'slack',
    normalized_name: 'slack',
    displayName: 'Slack',
    description: '',
    iconPath: '',
    category: 'app',
    toolCount: 1,
    tools: [],
    isConfigured: true,
    isAuthenticated: true,
    isFromRegistry: false,
    toolsetType: 'slack',
    authType: 'OAUTH',
    ...overrides,
  };
}

const slack = toolset({
  instanceId: 'sl',
  instanceName: 'Slack',
  tools: [
    { name: 'send_message', fullName: 'slack.send_message', description: '' },
    { name: 'search', fullName: 'slack.search', description: '' },
  ],
});
const gmail = toolset({
  name: 'gmail',
  toolsetType: 'gmail',
  instanceId: 'gm',
  instanceName: 'Gmail',
  tools: [{ name: 'send_email', fullName: 'gmail.send_email', description: '' }],
});
const jiraToConnect = toolset({
  name: 'jira',
  toolsetType: 'jira',
  instanceId: 'jr',
  instanceName: 'Jira Cloud',
  authType: 'API_TOKEN',
  isAuthenticated: false,
  tools: [{ name: 'search_issues', fullName: 'jira.search_issues', description: '' }],
});
const jiraConnected = { ...jiraToConnect, isAuthenticated: true };

/** What the backend holds: connected actions page by page (50 a page in the app), and the rest. */
let backend: { pages: BuilderSidebarToolset[][]; toConnect: BuilderSidebarToolset[]; holdPage2?: Promise<void> };
// Each test starts past the tab's 5-minute cache, so it loads again.
const realNow = Date.now.bind(Date);
let clockOffset = 0;

beforeEach(() => {
  installBrowserShims();
  clockOffset += 10 * 60 * 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => realNow() + clockOffset);
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false } });
  const store = useChatStore.getState();
  store.hydrateUniversalAgentResources(null);
  store.setUniversalAgentToolsLoading(false);
  store.setUniversalAgentToolsError(null);
  backend = { pages: [[slack]], toConnect: [jiraToConnect] };
  api.getMyToolsets.mockImplementation(async ({ page }: { page: number }) => {
    if (page === 2 && backend.holdPage2) await backend.holdPage2;
    return { toolsets: backend.pages[page - 1] ?? [], hasNext: page < backend.pages.length };
  });
  api.getAllMyToolsets.mockImplementation(async () => ({ toolsets: backend.toConnect }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

async function openActionsTab() {
  renderInTheme(<UniversalAgentResourcesPanel />);
  fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));
  return screen.findByTestId('toolset-connect-row');
}

/** Connect Jira through the dialog; the backend then lists it as connected. */
function connectJira(row: HTMLElement) {
  fireEvent.click(within(row).getByRole('button', { name: 'Connect' }));
  backend.pages[0] = [...backend.pages[0]!, jiraConnected];
  backend.toConnect = [];
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign in to Jira Cloud' })).getByRole('button', { name: 'Save' }));
}

describe('the chat’s Actions tab', () => {
  it('lists actions to connect, and a connected one joins with its tools on', async () => {
    // Picked by hand: one of Slack's two tools is on.
    useChatStore.getState().setUniversalAgentStreamTools(['sl:slack.send_message']);

    const row = await openActionsTab();
    expect(within(row).getByText('Jira Cloud')).toBeTruthy();
    expect(api.getAllMyToolsets).toHaveBeenCalledWith(expect.objectContaining({ authStatus: 'not-authenticated' }));

    connectJira(row);

    await waitFor(() =>
      expect(useChatStore.getState().universalAgentStreamTools).toEqual(['sl:slack.send_message', 'jr:jira.search_issues'])
    );
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
    expect(screen.getByText('Jira Cloud')).toBeTruthy();
    expect(toast.success).toHaveBeenCalledWith('Jira Cloud is connected');
  });

  it('still lists the connected actions when the ones to connect cannot be loaded', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    api.getAllMyToolsets.mockRejectedValue(new Error('down'));

    renderInTheme(<UniversalAgentResourcesPanel />);
    fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));

    expect(await screen.findByText('Slack')).toBeTruthy();
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
  });

  it('keeps the pages already loaded with Load more after a connect', async () => {
    backend.pages = [[slack], [gmail]];
    const row = await openActionsTab();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Gmail')).toBeTruthy();

    connectJira(row);

    await waitFor(() => expect(screen.queryByTestId('toolset-connect-row')).toBeNull());
    expect(screen.getByText('Jira Cloud')).toBeTruthy();
    expect(screen.getByText('Gmail')).toBeTruthy();
  });

  it('keeps showing the actions when the refresh after a connect fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const row = await openActionsTab();
    api.getMyToolsets.mockRejectedValue(new Error('down'));

    connectJira(row);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to load available actions.'));
    expect(screen.getByText('Slack')).toBeTruthy();
    expect(screen.queryByText('Failed to load available actions.')).toBeNull();
  });

  it('refreshes after a Load more that was still running when the sign-in finished', async () => {
    backend.pages = [[slack], [gmail]];
    let releasePage2: () => void = () => undefined;
    backend.holdPage2 = new Promise((resolve) => (releasePage2 = resolve));
    const row = await openActionsTab();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));

    connectJira(row);
    releasePage2();

    await waitFor(() => expect(screen.queryByTestId('toolset-connect-row')).toBeNull());
    expect(screen.getByText('Jira Cloud')).toBeTruthy();
    expect(screen.getByText('Gmail')).toBeTruthy();
  });
});
