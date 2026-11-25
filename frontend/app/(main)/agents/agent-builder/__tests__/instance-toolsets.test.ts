/**
 * U4 IT-1..IT-5: a second instance of one toolset type (P2.2). With
 * `INSTANCE_TOOL_IDS` off the builder keeps one instance per type, as the
 * server does; on, each instance is its own toolset, labelled by its name.
 */
import type { Edge, Node } from '@xyflow/react';
import type { TFunction } from 'i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { ToolsetsApi } from '@/app/(main)/toolsets/api';
import { useToolIdentityStore } from '@/app/(main)/toolsets/api/tool-identity';
import { handleFlowCanvasDrop } from '../components/canvas-drop-handler';
import { normalizeDisplayName } from '../display-utils';
import { extractAgentConfigFromFlow } from '../extract-agent-config';
import { isMcpAttachConflict } from '../sidebar-mcp-utils';
import { buildToolsetDragPayload, collectActiveToolsetTypeKeysFromNodes, isToolsetTypeConflict } from '../sidebar-toolset-utils';
import type { FlowNodeData } from '../types';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const t = ((key: string) => key) as unknown as TFunction;

const jira = (instanceId: string, instanceName: string): BuilderSidebarToolset =>
  ({
    instanceId,
    instanceName,
    name: 'jira',
    toolsetType: 'jira',
    displayName: 'Jira',
    description: '',
    iconPath: '',
    category: 'app',
    isConfigured: true,
    isAuthenticated: true,
    tools: [{ name: 'search_issues', fullName: 'jira.search_issues', description: 'Search' }],
  }) as unknown as BuilderSidebarToolset;

function drop(nodes: Node<FlowNodeData>[], payload: Record<string, string>) {
  let next = nodes;
  const errors: string[] = [];
  handleFlowCanvasDrop(
    {
      preventDefault: () => {},
      dataTransfer: { getData: (key: string) => payload[key] ?? '' },
    } as unknown as React.DragEvent,
    {
      flowPointer: { x: 0, y: 0 },
      nodes,
      setNodes: (update) => {
        next = typeof update === 'function' ? update(next) : update;
      },
      setEdges: (() => {}) as React.Dispatch<React.SetStateAction<Edge[]>>,
      nodeTemplates: [],
      configuredConnectors: [],
      activeAgentConnectors: [],
      readOnly: false,
      t,
      onError: (message) => errors.push(message),
    },
  );
  return { nodes: next, errors };
}

const toolsetNodes = (nodes: Node<FlowNodeData>[]) => nodes.filter((n) => String(n.data.type).startsWith('toolset-'));

beforeEach(() => useToolIdentityStore.setState({ instanceToolIds: false }));
afterEach(() => vi.clearAllMocks());

describe('a second instance of a toolset type', () => {
  it('IT-1: off, a second Jira instance is refused as today; two MCP instances of one type too', () => {
    const first = drop([], buildToolsetDragPayload(jira('inst-a', 'Acme Jira')));
    expect(toolsetNodes(first.nodes)).toHaveLength(1);
    const second = drop(first.nodes, buildToolsetDragPayload(jira('inst-b', 'Ops Jira')));
    expect(toolsetNodes(second.nodes)).toHaveLength(1);
    expect(second.errors).toEqual(['agentBuilder.toolsetDuplicateNotify']);
    expect(isToolsetTypeConflict(collectActiveToolsetTypeKeysFromNodes(first.nodes), 'jira', 'inst-b', false)).toBe(true);
    expect(isMcpAttachConflict(new Map([['github', 'm-1']]), 'm-2', 'github', false)).toBe(true);
  });

  it('IT-2: on, a second instance is its own node, labelled by its instance, and saved as its own toolset', () => {
    useToolIdentityStore.setState({ instanceToolIds: true });
    const first = drop([], buildToolsetDragPayload(jira('inst-a', 'Acme Jira')));
    const second = drop(first.nodes, buildToolsetDragPayload(jira('inst-b', 'Ops Jira')));
    expect(second.errors).toEqual([]);
    const nodes = toolsetNodes(second.nodes);
    expect(nodes.map((n) => n.data.label)).toEqual([normalizeDisplayName('Acme Jira'), normalizeDisplayName('Ops Jira')]);
    expect(nodes.map((n) => (n.data.config as { instanceId: string }).instanceId)).toEqual(['inst-a', 'inst-b']);

    const core = { id: 'core', data: { type: 'agent-core', label: 'Agent', config: {} } };
    const payload = extractAgentConfigFromFlow(
      'Two Jiras',
      [core, ...nodes],
      nodes.map((n) => ({ source: n.id, target: 'core', targetHandle: 'toolsets' })),
    );
    expect(payload.toolsets?.map((ts) => [ts.name, ts.instanceId, ts.instanceName])).toEqual([
      ['jira', 'inst-a', 'Acme Jira'],
      ['jira', 'inst-b', 'Ops Jira'],
    ]);
  });

  it('IT-3: on, the same instance again merges into its node', () => {
    useToolIdentityStore.setState({ instanceToolIds: true });
    const first = drop([], buildToolsetDragPayload(jira('inst-a', 'Acme Jira')));
    const again = drop(first.nodes, buildToolsetDragPayload(jira('inst-a', 'Acme Jira')));
    expect(toolsetNodes(again.nodes)).toHaveLength(1);
    expect(again.errors).toEqual([]);
    expect(isToolsetTypeConflict(new Set(['jira']), 'jira', undefined, true)).toBe(true);
  });

  it('IT-4: on, two MCP instances of one type are accepted; the same instance again merges', () => {
    const active = new Map([['github', 'm-1']]);
    expect(isMcpAttachConflict(active, 'm-2', 'github', true)).toBe(false);
    expect(isMcpAttachConflict(active, 'm-1', 'github', true)).toBe(false);
  });

  it('IT-5: the toolset lists report the flag; absent reads as off', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { toolsets: [], instanceToolIds: true } });
    await ToolsetsApi.getMyToolsets({ page: 1 });
    expect(useToolIdentityStore.getState().instanceToolIds).toBe(true);
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { toolsets: [] } });
    await ToolsetsApi.getAgentToolsets('agent-a', { page: 1 });
    expect(useToolIdentityStore.getState().instanceToolIds).toBe(false);
  });
});
