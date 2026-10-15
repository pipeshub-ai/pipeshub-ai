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
const jiraTools = [{ name: 'search_issues', fullName: 'jira.search_issues', description: '' }];
const jiraToConnect = toolset({
  name: 'jira',
  toolsetType: 'jira',
  displayName: 'Jira',
  instanceId: 'jr',
  instanceName: 'Jira Cloud',
  authType: 'API_TOKEN',
  isAuthenticated: false,
  tools: jiraTools,
});
const page = (toolsets: BuilderSidebarToolset[]) => ({ toolsets, hasNext: false });

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

async function openActionsTab() {
  renderInTheme(<UniversalAgentResourcesPanel />);
  fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));
}

describe('the chat’s Actions tab', () => {
  it('lists actions to connect, and a connected one joins with its tools on', async () => {
    api.getMyToolsets.mockResolvedValueOnce(page([slack]));
    api.getAllMyToolsets.mockResolvedValueOnce({ toolsets: [jiraToConnect] });
    // Picked by hand: one of Slack's two tools is on.
    useChatStore.getState().setUniversalAgentStreamTools(['sl:slack.send_message']);

    await openActionsTab();

    const row = await screen.findByTestId('toolset-connect-row');
    expect(within(row).getByText('Jira Cloud')).toBeTruthy();
    expect(api.getAllMyToolsets).toHaveBeenCalledWith(expect.objectContaining({ authStatus: 'not-authenticated' }));

    api.getMyToolsets.mockResolvedValueOnce(page([slack, { ...jiraToConnect, isAuthenticated: true }]));
    api.getAllMyToolsets.mockResolvedValueOnce({ toolsets: [] });
    fireEvent.click(within(row).getByRole('button', { name: 'Connect' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign in to Jira Cloud' })).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(useChatStore.getState().universalAgentStreamTools).toEqual(['sl:slack.send_message', 'jr:jira.search_issues'])
    );
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
    expect(screen.getByText('Jira Cloud')).toBeTruthy();
    expect(toast.success).toHaveBeenCalledWith('Jira Cloud is connected');
  });

  it('still lists the connected actions when the ones to connect cannot be loaded', async () => {
    // Past the tab's cache from the test above, so it loads again.
    const later = Date.now() + 10 * 60 * 1000;
    vi.spyOn(Date, 'now').mockReturnValue(later);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    api.getMyToolsets.mockResolvedValueOnce(page([slack]));
    api.getAllMyToolsets.mockRejectedValueOnce(new Error('down'));

    await openActionsTab();

    expect(await screen.findByText('Slack')).toBeTruthy();
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
  });
});
