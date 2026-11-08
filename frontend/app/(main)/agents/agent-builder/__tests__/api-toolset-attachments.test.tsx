import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, renderHook, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import { toolset } from '../../../workspace/api-toolsets/__tests__/fixtures';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { AgentDetail } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/sidebar-web-search-section', () => ({ AgentBuilderWebSearchSection: () => null }));

import {
  API_TOOLSET_CATEGORY,
  API_TOOLSET_TYPE,
  apiToolsetSidebarRow,
  changedAttachments,
  isApiToolsetRow,
} from '../api-toolset-attachments';
import {
  buildToolDragPayload,
  buildToolsetDragPayload,
  collectActiveToolsetTypeKeysFromNodes,
} from '../sidebar-toolset-utils';
import { extractAgentConfigFromFlow } from '../extract-agent-config';
import { useAgentBuilderReconstruction } from '../hooks/use-flow-reconstruction';
import { AgentBuilderToolsetsSection } from '../components/sidebar-toolsets-section';

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);
const orders = toolset({ id: A, isConnected: true });

beforeEach(installBrowserShims);
afterEach(cleanup);

/** A canvas toolset node as the drop handler builds it from a sidebar row. */
const nodeFrom = (id: string, row: BuilderSidebarToolset, toolNames: string[]) => {
  const payload = buildToolsetDragPayload(row);
  return {
    id,
    data: {
      type: payload['application/reactflow'],
      config: {
        instanceId: row.instanceId,
        toolsetName: payload.toolsetName,
        category: payload.category,
        tools: row.tools.filter((tool) => toolNames.includes(tool.name)),
      },
    },
  };
};
const core = { id: 'agent-core-1', data: { type: 'agent-core', config: {} } };
const toCore = (source: string) => ({ source, target: 'agent-core-1', targetHandle: 'toolsets' });

describe('an API toolset in the sidebar', () => {
  it('is an action row whose tools are named "<toolsetId>:<toolName>"', () => {
    const row = apiToolsetSidebarRow(orders);
    expect(isApiToolsetRow(row)).toBe(true);
    expect(row).toMatchObject({
      instanceId: A,
      instanceName: 'Orders',
      toolsetType: API_TOOLSET_TYPE,
      category: API_TOOLSET_CATEGORY,
      isConfigured: true,
      isAuthenticated: true,
    });
    expect(row.tools.map((tool) => tool.fullName)).toEqual([`${A}:listOrders`, `${A}:createOrder`]);
    expect(apiToolsetSidebarRow(toolset()).isAuthenticated).toBe(false);
  });

  it('drags with its own tool names, never the "<type>.<tool>" fallback', () => {
    const row = apiToolsetSidebarRow(orders);
    const whole = buildToolsetDragPayload(row);
    expect(whole.category).toBe(API_TOOLSET_CATEGORY);
    expect(JSON.parse(whole.allTools).map((tool: { fullName: string }) => tool.fullName)).toEqual([
      `${A}:listOrders`,
      `${A}:createOrder`,
    ]);
    expect(buildToolDragPayload(row.tools[0], row).fullName).toBe(`${A}:listOrders`);
  });

  it('never counts as a duplicate type, so two toolsets can sit on one canvas', () => {
    const nodes = [
      nodeFrom('n1', apiToolsetSidebarRow(orders), ['listOrders']),
      nodeFrom('n2', apiToolsetSidebarRow(toolset({ id: B })), ['listOrders']),
    ];
    expect(collectActiveToolsetTypeKeysFromNodes(nodes).size).toBe(0);
  });
});

describe('saving an agent', () => {
  const jira = {
    id: 'jira-1',
    data: {
      type: 'toolset-jira',
      config: { toolsetName: 'jira', instanceId: 'j1', tools: [{ name: 'search', fullName: 'jira.search' }] },
    },
  };

  it('sends connected API toolset nodes as apiToolsetTools, not as toolsets', () => {
    const nodes = [
      core,
      jira,
      nodeFrom('api-1', apiToolsetSidebarRow(orders), ['listOrders', 'createOrder']),
      nodeFrom('api-2', apiToolsetSidebarRow(toolset({ id: B })), ['listOrders']),
    ];
    const payload = extractAgentConfigFromFlow('A', nodes, [toCore('jira-1'), toCore('api-1')], null);
    expect(payload.apiToolsetTools).toEqual([`${A}:listOrders`, `${A}:createOrder`]);
    expect(payload.toolsets.map((ts) => ts.instanceId)).toEqual(['j1']);
  });

  it('leaves the list out of an update that did not change it', () => {
    const stored = { apiToolsetTools: [`${A}:listOrders`] } as AgentDetail;
    const nodes = [core, nodeFrom('api-1', apiToolsetSidebarRow(orders), ['listOrders'])];
    expect(extractAgentConfigFromFlow('A', nodes, [toCore('api-1')], stored)).not.toHaveProperty('apiToolsetTools');
    expect(extractAgentConfigFromFlow('A', [core], [], stored).apiToolsetTools).toEqual([]);
  });

  it('compares lists ignoring order and repeats', () => {
    expect(changedAttachments([`${B}:x`, `${A}:x`, `${A}:x`], [`${A}:x`, `${B}:x`])).toEqual({});
    expect(changedAttachments([`${A}:x`], undefined)).toEqual({ apiToolsetTools: [`${A}:x`] });
    expect(changedAttachments([], [`${A}:x`])).toEqual({ apiToolsetTools: [] });
  });
});

describe('opening a saved agent', () => {
  const agent = {
    name: 'A',
    toolsets: [],
    knowledge: [],
    models: [],
    apiToolsetTools: [`${A}:listOrders`, `${B}:gone`],
  } as unknown as AgentDetail;

  const rebuild = () => {
    const { result } = renderHook(() => useAgentBuilderReconstruction());
    return result.current.reconstructFlowFromAgent(agent, [], [], [], [orders]);
  };

  it('draws one toolset node per API toolset, connected to the agent', () => {
    const { nodes, edges } = rebuild();
    const apiNodes = nodes.filter((n) => n.data.config?.category === API_TOOLSET_CATEGORY);
    expect(apiNodes.map((n) => n.data.label)).toEqual(['Orders', 'API actions']);
    expect(apiNodes.every((n) => n.data.type === `toolset-${API_TOOLSET_TYPE}`)).toBe(true);

    const [known, missing] = apiNodes.map((n) => n.data.config!);
    expect(known.instanceId).toBe(A);
    expect(known.selectedTools).toEqual(['listOrders']);
    expect((known.availableTools as { name: string }[]).map((tool) => tool.name)).toEqual(['listOrders', 'createOrder']);
    // A toolset that's gone keeps its entry, so saving doesn't drop it unseen.
    expect(missing.selectedTools).toEqual(['gone']);

    const linked = edges.filter((e) => e.target === 'agent-core-1' && e.targetHandle === 'toolsets').map((e) => e.source);
    expect(apiNodes.every((n) => linked.includes(n.id))).toBe(true);
    expect(nodes.find((n) => n.data.type === 'agent-core')?.data.config).not.toHaveProperty('apiToolsetTools');
  });

  it('saves back unchanged', () => {
    const { nodes, edges } = rebuild();
    const payload = extractAgentConfigFromFlow('A', nodes, edges, agent);
    expect(payload).not.toHaveProperty('apiToolsetTools');
    expect(payload.toolsets).toEqual([]);
  });
});

describe('API toolsets in the Tools section', () => {
  const renderPalette = (rows: BuilderSidebarToolset[], isServiceAccount = false) => {
    const onNotify = vi.fn();
    renderInTheme(
      <AgentBuilderToolsetsSection
        toolsets={rows}
        loading={false}
        refreshToolsets={async () => {}}
        activeToolsetTypeKeys={new Set()}
        toolsetMergeCheckNodes={[]}
        isServiceAccount={isServiceAccount}
        agentKey={null}
        onNotify={onNotify}
        webSearchAttached={null}
      />,
    );
    const drag = (label: string) => {
      const row = screen.getByText(label).closest('[draggable]') as HTMLElement;
      fireEvent.dragStart(row, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } });
    };
    return { onNotify, drag };
  };

  it('groups them under one heading', () => {
    renderPalette([apiToolsetSidebarRow(orders), apiToolsetSidebarRow(toolset({ id: B, name: 'Billing' }))]);
    expect(screen.getAllByText('API actions')).toHaveLength(1);
    expect(screen.getByText('Orders')).toBeTruthy();
    expect(screen.getByText('Billing')).toBeTruthy();
  });

  it('drags a connected one and says a toolset is not connected yet', () => {
    const { onNotify, drag } = renderPalette([
      apiToolsetSidebarRow(orders),
      apiToolsetSidebarRow(toolset({ id: B, name: 'Billing' })),
    ]);
    drag('Orders');
    expect(onNotify).not.toHaveBeenCalled();
    drag('Billing');
    expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('Billing is not authenticated'));
  });

  it('keeps per-member toolsets off a service-account agent but allows shared ones', () => {
    const { onNotify, drag } = renderPalette(
      [
        apiToolsetSidebarRow(orders),
        apiToolsetSidebarRow(toolset({ id: B, name: 'Shared', isConnected: true, useSharedCredential: true })),
      ],
      true,
    );
    drag('Shared');
    expect(onNotify).not.toHaveBeenCalled();
    drag('Orders');
    expect(onNotify).toHaveBeenCalledWith('Service-account agents can only use API actions with a shared credential or no sign-in.');
  });

  it('shows them above the "Show more" cut, however many app actions are connected', () => {
    const { apiToolset: _, ...connected } = apiToolsetSidebarRow(orders);
    const appRow = (type: string): BuilderSidebarToolset => ({
      ...connected,
      name: type,
      normalized_name: type,
      displayName: type,
      category: 'app',
      toolsetType: type,
      instanceId: `${type}-instance`,
      instanceName: type,
    });
    renderPalette([...['jira', 'slack', 'gmail', 'notion', 'linear', 'github'].map(appRow), apiToolsetSidebarRow(orders)]);
    expect(screen.getByText('API actions')).toBeTruthy();
    expect(screen.getByText(/Show more/)).toBeTruthy();
  });

  it('filters them by the search box', () => {
    renderPalette([apiToolsetSidebarRow(orders), apiToolsetSidebarRow(toolset({ id: B, name: 'Billing' }))]);
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'bill' } });
    expect(screen.queryByText('Orders')).toBeNull();
    expect(screen.getByText('Billing')).toBeTruthy();
  });
});
