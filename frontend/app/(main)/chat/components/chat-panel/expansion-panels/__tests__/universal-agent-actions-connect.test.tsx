import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { ApiToolset } from '@/app/(main)/workspace/api-toolsets/types';

const api = vi.hoisted(() => ({
  getMyToolsets: vi.fn(),
  getAllMyToolsets: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));
const apiActionsApi = vi.hoisted(() => ({ listInstances: vi.fn() }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api, MAX_TOOLSETS_LIST_LIMIT: 200 }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({
  McpServersApi: { getMyMcpServers: vi.fn(async () => ({ instances: [] })) },
}));
vi.mock('@/app/(main)/workspace/api-toolsets/api', () => ({ ApiToolsetsApi: apiActionsApi }));
// The real dialog saves the credential; here "Save" stands for one that was accepted.
vi.mock('@/app/(main)/workspace/api-toolsets/components', () => ({
  ApiActionConnectionPanel: ({ toolset, onChanged }: { toolset: ApiToolset | null; onChanged: () => void }) =>
    toolset ? (
      <div role="dialog" aria-label={`Connect ${toolset.name}`}>
        <button type="button" onClick={onChanged}>
          Save
        </button>
      </div>
    ) : null,
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
  apiActionsApi.listInstances.mockResolvedValue([]);
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

const ID_A = 'a'.repeat(32);
const ID_B = 'b'.repeat(32);

function apiAction(overrides: Partial<ApiToolset>): ApiToolset {
  return {
    id: ID_A,
    name: 'Orders API',
    description: '',
    specTitle: 'Orders',
    authType: 'api_key',
    useSharedCredential: false,
    isConnected: true,
    operations: [{ toolName: 'listOrders', method: 'get', path: '/orders', summary: 'List orders', kind: 'read' }],
    updatedAt: 0,
    ...overrides,
  };
}

describe('API actions in the chat’s Actions tab', () => {
  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_API_TOOLSETS: true } });
  });

  it('lists connected API actions as groups and offers to connect the rest', async () => {
    apiActionsApi.listInstances.mockResolvedValue([
      apiAction({}),
      apiAction({ id: ID_B, name: 'Billing API', isConnected: false }),
    ]);

    await openActionsTab();
    expect(screen.getByText('Orders API')).toBeTruthy();
    expect(useChatStore.getState().universalAgentToolCatalogFullNames).toContain(`${ID_A}:${ID_A}:listOrders`);
    expect(within(await screen.findByTestId('api-action-connect-row')).getByText('Billing API')).toBeTruthy();
    // The Jira row above carries the "Not connected" heading.
    expect(screen.getAllByText('Not connected')).toHaveLength(1);
  });

  it('turns a newly connected API action’s operations on for a hand-picked selection', async () => {
    backend.toConnect = [];
    apiActionsApi.listInstances.mockResolvedValue([apiAction({ isConnected: false })]);
    useChatStore.getState().setUniversalAgentStreamTools(['sl:slack.send_message']);

    renderInTheme(<UniversalAgentResourcesPanel />);
    fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));
    const row = await screen.findByTestId('api-action-connect-row');
    fireEvent.click(within(row).getByRole('button', { name: 'Connect' }));
    apiActionsApi.listInstances.mockResolvedValue([apiAction({})]);
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Connect Orders API' })).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(useChatStore.getState().universalAgentStreamTools).toEqual([
        'sl:slack.send_message',
        `${ID_A}:${ID_A}:listOrders`,
      ])
    );
    expect(screen.queryByTestId('api-action-connect-row')).toBeNull();
  });

  it('shows a shared-credential action a member cannot connect as waiting for an admin', async () => {
    apiActionsApi.listInstances.mockResolvedValue([apiAction({ useSharedCredential: true, isConnected: false })]);

    await openActionsTab();
    const row = await screen.findByTestId('api-action-connect-row');
    expect(within(row).getByText('Waiting for admin')).toBeTruthy();
    expect(within(row).queryByRole('button', { name: 'Connect' })).toBeNull();
  });

  it('lists the other actions when API actions cannot be loaded', async () => {
    // A failure keeps the last list, and the panel's cache outlives a test: start from an empty one.
    await openActionsTab();
    cleanup();
    clockOffset += 10 * 60 * 1000;
    apiActionsApi.listInstances.mockRejectedValue(new Error('labs flag off'));

    await openActionsTab();
    expect(screen.getByText('Slack')).toBeTruthy();
    expect(screen.queryByTestId('api-action-connect-row')).toBeNull();
  });

  it('keeps the API actions it had when a refresh fails', async () => {
    apiActionsApi.listInstances.mockResolvedValue([apiAction({})]);
    await openActionsTab();
    expect(await screen.findByText('Orders API')).toBeTruthy();
    cleanup();

    clockOffset += 10 * 60 * 1000;
    apiActionsApi.listInstances.mockRejectedValue(new Error('network blip'));
    await openActionsTab();
    await waitFor(() => expect(apiActionsApi.listInstances).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Orders API')).toBeTruthy();
  });

  it('finds an operation by the summary its row shows', async () => {
    apiActionsApi.listInstances.mockResolvedValue([apiAction({})]);
    await openActionsTab();
    fireEvent.change(screen.getByPlaceholderText('Search actions'), { target: { value: 'list orders' } });
    expect(await screen.findByText('Orders API')).toBeTruthy();
  });

  it('loads API actions once the flag arrives after the panel opened', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false } });
    apiActionsApi.listInstances.mockResolvedValue([apiAction({})]);
    await openActionsTab();
    expect(apiActionsApi.listInstances).not.toHaveBeenCalled();

    act(() => {
      useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_API_TOOLSETS: true } });
    });
    expect(await screen.findByText('Orders API')).toBeTruthy();
  });

  it('does not ask for API actions with the flag off', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false } });

    await openActionsTab();
    expect(apiActionsApi.listInstances).not.toHaveBeenCalled();
  });
});
