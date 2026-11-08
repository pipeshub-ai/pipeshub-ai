import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import { buildAgentChatMcpGroups } from '@/app/(main)/agents/api';
import type { AgentDetail } from '@/app/(main)/agents/types';

const api = vi.hoisted(() => ({ getNodeChildren: vi.fn(), searchNodes: vi.fn() }));

vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: {
    getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args),
    searchNodes: (...args: unknown[]) => api.searchNodes(...args),
  },
}));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));

import { AgentScopedResourcesPanel } from '../chat-panel/expansion-panels/agent-scoped-resources-panel';

const drive = { id: 'drive-1', label: 'Engineering Drive', kind: 'Drive' };

beforeEach(() => {
  api.getNodeChildren.mockResolvedValue({ items: [], pagination: { nextCursor: null } });
  useChatStore.setState({
    collectionMetaCache: {},
    collectionNamesCache: {},
    agentSidebarAgentId: 'agent-1',
    agentKnowledgeScope: null,
    agentChatConnectors: [
      { id: 'jira-1', label: 'Jira', connectorKind: 'Jira' },
      { id: 'group-x', label: 'Specs space', connectorKind: 'Drive', nodeType: 'recordGroup', source: drive },
      { id: 'folder-y', label: 'Roadmaps', connectorKind: 'Drive', nodeType: 'folder', source: drive },
    ],
    agentKnowledgeCollectionRows: [],
    agentKnowledgeDefaults: { apps: ['jira-1'], kb: [], recordGroups: ['group-x'], records: ['folder-y'] },
    agentChatToolGroups: [],
    agentChatMcpGroups: [],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel() {
  return render(
    <Theme>
      <AgentScopedResourcesPanel />
    </Theme>
  );
}

describe('Agent chat sources panel', () => {
  it('lists the nodes a connector is limited to under that connector, by name', () => {
    renderPanel();

    expect(screen.getByText('· 2 selected')).toBeTruthy();
    const group = screen.getByText('Engineering Drive').parentElement!.parentElement!;
    expect(within(group).getByText('Specs space')).toBeTruthy();
    expect(within(group).getByText('Roadmaps')).toBeTruthy();
    // A connector used as a whole is a row of its own, not under a heading.
    expect(screen.getByText('Jira')).toBeTruthy();
    expect(within(group).queryByText('Jira')).toBeNull();

    // The heading folds its nodes away like a connector row, with no checkbox of its own.
    expect(within(group).getAllByRole('checkbox')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Hide what is in Engineering Drive' }));
    expect(screen.getByText('Specs space').closest('[style*="display: none"]')).toBeTruthy();
  });

  it('counts what a connector is limited to, not what the search left of it', () => {
    renderPanel();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'roadmaps' } });

    expect(screen.queryByText('Specs space')).toBeNull();
    expect(screen.getByText('Roadmaps')).toBeTruthy();
    expect(screen.getByText('· 2 selected')).toBeTruthy();
  });

  it('keeps a connector\'s nodes when the search matches the connector name', () => {
    renderPanel();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'engineering' } });

    expect(screen.getByText('Specs space')).toBeTruthy();
    expect(screen.getByText('Roadmaps')).toBeTruthy();
    expect(screen.queryByText('Jira')).toBeNull();
  });

  it('searches under the agent\'s own sources, not everywhere', async () => {
    api.searchNodes.mockResolvedValue({ items: [], pagination: { nextCursor: null } });
    renderPanel();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'budget' } });

    await waitFor(() => expect(api.searchNodes).toHaveBeenCalledTimes(3));
    const parents = api.searchNodes.mock.calls.map(([params]) => (params as { parent: { id: string } }).parent.id);
    expect(parents.sort()).toEqual(['folder-y', 'group-x', 'jira-1']);
    for (const [params] of api.searchNodes.mock.calls) {
      expect((params as { origins?: string }).origins).toBeUndefined();
    }
    await waitFor(() => expect(screen.getByText('No connectors found')).toBeTruthy());
    expect(within(document.body).queryByText('Specs space')).toBeNull();
  });
});

describe('Agent chat MCP tab', () => {
  it("shows an attached server's own logo, on its tools too", () => {
    const agent = {
      mcpServers: [
        {
          _key: 'srv-1',
          name: 'atlassian_rovo',
          // Renamed in the builder: the logo still comes from the catalog type.
          displayName: 'Team wiki',
          typeId: 'atlassian_rovo',
          instanceId: 'inst-1',
          tools: [{ name: 'search', fullName: 'mcp_atlassian_rovo_search' }],
        },
      ],
    } as unknown as AgentDetail;
    useChatStore.setState({ agentChatMcpGroups: buildAgentChatMcpGroups(agent) });
    renderPanel();

    fireEvent.click(screen.getByRole('tab', { name: 'MCP' }));
    expect(screen.getByText('Team wiki')).toBeTruthy();
    expect(screen.getAllByRole('img', { name: 'atlassian icon' })).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(screen.getAllByRole('img', { name: 'atlassian icon' })).toHaveLength(2);
  });
});
