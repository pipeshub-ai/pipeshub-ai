/**
 * An agent's own chat lists the agent's actions the person has not signed in to under "Not connected", with Connect,
 * as the main chat's Actions tab does. They were shown ticked and ready, and the answer ran without them.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

const api = vi.hoisted(() => ({
  getAllMyToolsets: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api, MAX_TOOLSETS_LIST_LIMIT: 200 }));
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
import { AgentScopedResourcesPanel } from '../agent-scoped-resources-panel';

const jiraGroup = { label: 'Jira Cloud', fullNames: ['jr:jira.search_issues', 'jr:jira.create_issue'], toolsetSlug: 'jira', instanceId: 'jr' };
const slackGroup = { label: 'Slack', fullNames: ['sl:slack.send_message'], toolsetSlug: 'slack', instanceId: 'sl' };
const jiraToConnect: BuilderSidebarToolset = {
  name: 'jira',
  normalized_name: 'jira',
  displayName: 'Jira',
  description: '',
  iconPath: '',
  category: 'app',
  toolCount: 2,
  tools: [],
  isConfigured: true,
  isAuthenticated: false,
  isFromRegistry: false,
  toolsetType: 'jira',
  authType: 'API_TOKEN',
  instanceId: 'jr',
  instanceName: 'Jira Cloud',
};
// Not one of the agent's: the main chat's tab lists it, an agent's own chat does not.
const gmailToConnect: BuilderSidebarToolset = { ...jiraToConnect, name: 'gmail', toolsetType: 'gmail', instanceId: 'gm', instanceName: 'Gmail' };

let toConnect: BuilderSidebarToolset[];

function hydrate(isServiceAccount = false) {
  useChatStore.getState().hydrateAgentChatResources({
    toolCatalogFullNames: [...jiraGroup.fullNames, ...slackGroup.fullNames],
    toolGroups: [jiraGroup, slackGroup],
    mcpGroups: [],
    connectors: [],
    kbIds: [],
    knowledgeCollectionRows: [],
    knowledgeDefaults: { apps: [], kb: [] },
    deprecatedToolNames: [],
    isServiceAccount,
  });
}

const openActions = () => {
  renderInTheme(<AgentScopedResourcesPanel />);
  fireEvent.click(screen.getByRole('tab', { name: 'Actions' }));
};

beforeEach(() => {
  installBrowserShims();
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false } });
  toConnect = [jiraToConnect, gmailToConnect];
  api.getAllMyToolsets.mockImplementation(async () => ({ toolsets: toConnect }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useChatStore.getState().hydrateAgentChatResources(null);
});

describe('an agent chat’s Actions tab', () => {
  it('lists the agent’s actions you have not signed in to under Not connected, with Connect, and not as ready', async () => {
    hydrate();
    openActions();

    const row = await screen.findByTestId('toolset-connect-row');
    expect(within(row).getByText('Jira Cloud')).toBeTruthy();
    expect(within(row).getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(screen.getByText('Not connected')).toBeTruthy();
    expect(screen.getAllByTestId('toolset-connect-row')).toHaveLength(1);
    expect(screen.queryByText('Gmail')).toBeNull();
    // Slack is signed in: it stays a ready group with its tools; Jira is no longer one.
    expect(screen.getAllByText('Slack').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Jira Cloud')).toHaveLength(1);
    expect(api.getAllMyToolsets).toHaveBeenCalledWith(expect.objectContaining({ authStatus: 'not-authenticated' }));
  });

  it('moves an action back to the ready list once you connect it', async () => {
    hydrate();
    openActions();
    const row = await screen.findByTestId('toolset-connect-row');

    fireEvent.click(within(row).getByRole('button', { name: 'Connect' }));
    toConnect = [gmailToConnect];
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign in to Jira Cloud' })).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByTestId('toolset-connect-row')).toBeNull());
    expect(screen.getAllByText('Jira Cloud').length).toBeGreaterThan(0);
    expect(toast.success).toHaveBeenCalledWith('Jira Cloud is connected');
  });

  it('offers no Connect for an agent that runs with its own credentials', async () => {
    hydrate(true);
    openActions();

    expect(await screen.findAllByText('Jira Cloud')).toHaveLength(1);
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
    expect(api.getAllMyToolsets).not.toHaveBeenCalled();
  });

  it('still lists the agent’s actions when the ones to connect cannot be loaded', async () => {
    api.getAllMyToolsets.mockRejectedValue(new Error('down'));
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    hydrate();
    openActions();

    expect(await screen.findAllByText('Jira Cloud')).toHaveLength(1);
    expect(screen.queryByTestId('toolset-connect-row')).toBeNull();
  });
});
