import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import '@/lib/__tests__/test-i18n';
import testI18n from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import type { FlowNodeData } from '../types';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';

const reactFlow = vi.hoisted(() => ({ setNodes: vi.fn() }));
vi.mock('@xyflow/react', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@xyflow/react')>()),
  useReactFlow: () => reactFlow,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/node-handles', () => ({ NodeHandles: () => null }));
const mcpApi = vi.hoisted(() => ({ getAgentToolRules: vi.fn(), updateAgentToolRules: vi.fn() }));
vi.mock('../../../workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));

import { handleFlowCanvasDrop } from '../components/canvas-drop-handler';
import { McpFlowNode } from '../components/mcp-flow-node';
import { McpAgentRulesContext, McpLiveServersContext } from '../mcp-live-servers';
import { AgentBuilderMcpSection } from '../components/sidebar-mcp-section';
import { extractAgentConfigFromFlow } from '../extract-agent-config';
import {
  buildMcpServerDragPayload,
  buildMcpToolDragPayload,
  findMcpServersWithoutTools,
  mergeMcpDropIntoConfig,
  type McpFlowTool,
} from '../sidebar-mcp-utils';

const LIST: McpFlowTool = { name: 'list_issues', fullName: 'mcp_github_list_issues', description: '' };
const CREATE: McpFlowTool = { name: 'create_pr', fullName: 'mcp_github_create_pr', description: '' };

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'github',
    typeId: 'github',
    transport: 'streamable_http',
    authMode: 'none',
    useAdminAuth: false,
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [
      { name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {} },
      { name: 'create_pr', namespacedName: 'mcp_github_create_pr', inputSchema: {} },
    ],
    ...overrides,
  };
}

function mcpNode(config: Record<string, unknown>, id = 'mcp-node'): Node<FlowNodeData> {
  return {
    id,
    type: 'flowNode',
    position: { x: 0, y: 0 },
    data: {
      id,
      type: 'mcp-inst-1',
      label: 'github',
      description: '',
      icon: 'hub',
      category: 'mcp-server',
      config: { instanceId: 'inst-1', name: 'github', displayName: 'github', typeId: 'github', ...config },
      inputs: [],
      outputs: ['output'],
      isConfigured: true,
    },
  } as Node<FlowNodeData>;
}

function drop(
  payload: Record<string, string>,
  nodes: Node<FlowNodeData>[] = [],
  extra: { mcpDropBlockedMessage?: string } = {}
) {
  let current = nodes;
  const setNodes = vi.fn((update: React.SetStateAction<Node<FlowNodeData>[]>) => {
    current = typeof update === 'function' ? update(current) : update;
  });
  const onError = vi.fn();
  const event = {
    preventDefault: vi.fn(),
    dataTransfer: { getData: (key: string) => payload[key] ?? '' },
  } as unknown as React.DragEvent;
  handleFlowCanvasDrop(event, {
    flowPointer: { x: 0, y: 0 },
    nodes,
    setNodes,
    setEdges: vi.fn(),
    nodeTemplates: [],
    configuredConnectors: [],
    activeAgentConnectors: [],
    readOnly: false,
    t: testI18n.t.bind(testI18n),
    onError,
    ...extra,
  });
  return { nodes: current, onError };
}

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('dropping MCP servers and tools', () => {
  it('a whole server attaches all of its tools', () => {
    const { nodes } = drop(buildMcpServerDragPayload(entry()));
    const cfg = nodes[0]!.data.config!;
    expect(cfg.allTools).toBe(true);
    expect((cfg.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['list_issues', 'create_pr']);
  });

  it('one tool starts a node with just that tool and the rest available', () => {
    const { nodes } = drop(buildMcpToolDragPayload(entry(), entry().tools[1]!));
    const cfg = nodes[0]!.data.config!;
    expect(cfg.allTools).toBe(false);
    expect((cfg.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['create_pr']);
    expect((cfg.availableTools as McpFlowTool[]).map((t) => t.name)).toEqual(['list_issues', 'create_pr']);
  });

  it('another tool of the same server joins its node', () => {
    const existing = mcpNode({ allTools: false, tools: [CREATE], availableTools: [LIST, CREATE] });
    const { nodes, onError } = drop(buildMcpToolDragPayload(entry(), entry().tools[0]!), [existing]);
    expect(nodes).toHaveLength(1);
    expect((nodes[0]!.data.config!.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['create_pr', 'list_issues']);
    expect(onError).not.toHaveBeenCalled();
  });

  it('the whole server dropped on its node turns on all tools', () => {
    const existing = mcpNode({ allTools: false, tools: [CREATE] });
    const { nodes, onError } = drop(buildMcpServerDragPayload(entry()), [existing]);
    expect(nodes[0]!.data.config!.allTools).toBe(true);
    expect(onError).not.toHaveBeenCalled();
  });

  it('another server of the same type is still refused', () => {
    const existing = mcpNode({ allTools: true, tools: [LIST] });
    const { nodes, onError } = drop(buildMcpServerDragPayload(entry({ _id: 'inst-2' })), [existing]);
    expect(nodes).toEqual([existing]);
    expect(onError).toHaveBeenCalledOnce();
  });

  it("is refused, with the reason, while the agent's MCP servers couldn't be read", () => {
    const { nodes, onError } = drop(buildMcpServerDragPayload(entry()), [], { mcpDropBlockedMessage: 'Reload first' });
    expect(nodes).toEqual([]);
    expect(onError).toHaveBeenCalledWith('Reload first');
  });

  it('a tool dropped on an all-tools node changes nothing', () => {
    const config = { allTools: true, tools: [LIST, CREATE], availableTools: [LIST, CREATE] };
    expect(mergeMcpDropIntoConfig(config, [LIST, CREATE], LIST)).toMatchObject({ allTools: true, tools: [LIST, CREATE] });
  });
});

describe('McpFlowNode', () => {
  function renderNode(config: Record<string, unknown>) {
    const node = mcpNode(config);
    renderInTheme(<McpFlowNode id={node.id} data={node.data} selected={false} />);
    return node;
  }

  function applyLastUpdate(node: Node<FlowNodeData>) {
    const update = reactFlow.setNodes.mock.calls.at(-1)![0] as (nodes: Node<FlowNodeData>[]) => Node<FlowNodeData>[];
    return update([node])[0]!.data.config!;
  }

  it('removing a tool while all tools are on keeps the rest, explicitly', () => {
    const node = renderNode({ allTools: true, tools: [LIST, CREATE], availableTools: [LIST, CREATE] });
    fireEvent.click(screen.getByRole('button', { name: 'Remove list_issues' }));
    const cfg = applyLastUpdate(node);
    expect(cfg.allTools).toBe(false);
    expect((cfg.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['create_pr']);
  });

  it('offers the tools not yet selected', async () => {
    const node = renderNode({ allTools: false, tools: [LIST], availableTools: [LIST, CREATE] });
    fireEvent.click(screen.getByRole('button', { name: 'Add tools' }));
    fireEvent.click(await screen.findByText('create pr'));
    expect((applyLastUpdate(node).tools as McpFlowTool[]).map((t) => t.name)).toEqual(['list_issues', 'create_pr']);
  });

  it('turning on all tools selects everything available', () => {
    const node = renderNode({ allTools: false, tools: [LIST], availableTools: [LIST, CREATE] });
    fireEvent.click(screen.getByRole('switch'));
    const cfg = applyLastUpdate(node);
    expect(cfg.allTools).toBe(true);
    expect((cfg.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['list_issues', 'create_pr']);
  });
});

describe('McpFlowNode with the live server', () => {
  function renderLive(config: Record<string, unknown>, servers: McpMyServerEntry[] | null) {
    const node = mcpNode(config);
    renderInTheme(
      <McpLiveServersContext.Provider value={servers}>
        <McpFlowNode id={node.id} data={node.data} selected={false} />
      </McpLiveServersContext.Provider>
    );
    return node;
  }

  it('flags a saved tool the server no longer offers', () => {
    renderLive({ allTools: false, tools: [LIST, CREATE] }, [entry({ tools: [entry().tools![0]!] })]);
    expect(screen.getAllByText('Not on the server')).toHaveLength(1);
  });

  it('offers a tool the server added since the agent was saved', async () => {
    renderLive({ allTools: false, tools: [LIST], availableTools: [LIST] }, [entry()]);
    fireEvent.click(screen.getByRole('button', { name: 'Add tools' }));
    expect(await screen.findByText('create pr')).toBeTruthy();
  });

  it('with all tools on, shows what the server offers now', () => {
    renderLive({ allTools: true, tools: [LIST], availableTools: [LIST] }, [entry()]);
    expect(screen.getByText('list issues')).toBeTruthy();
    expect(screen.getByText('create pr')).toBeTruthy();
  });

  it('marks a server that is no longer available', () => {
    renderLive({ allTools: true, tools: [LIST] }, []);
    expect(screen.getByText('Not available')).toBeTruthy();
    expect(screen.getByText(/isn't among your MCP servers/)).toBeTruthy();
  });

  it("shows a server's state when it isn't ready, and keeps the saved tools", () => {
    renderLive({ allTools: false, tools: [LIST] }, [entry({ authMode: 'oauth', isAuthenticated: false, tools: [] })]);
    expect(screen.getByText('Not connected')).toBeTruthy();
    expect(screen.getByText('list issues')).toBeTruthy();
    expect(screen.queryByText('Not on the server')).toBeNull();
  });

  it('without a list shows what was saved, unmarked', () => {
    renderLive({ allTools: false, tools: [LIST] }, null);
    expect(screen.queryByText('Not available')).toBeNull();
    expect(screen.queryByText('Not on the server')).toBeNull();
  });

  it('reading the live server changes nothing on the canvas', () => {
    renderLive({ allTools: true, tools: [LIST], availableTools: [LIST] }, [entry()]);
    expect(reactFlow.setNodes).not.toHaveBeenCalled();
  });

  it('turning on all tools takes what the server offers now', () => {
    const node = renderLive({ allTools: false, tools: [LIST], availableTools: [LIST] }, [entry()]);
    fireEvent.click(screen.getByRole('switch'));
    const update = reactFlow.setNodes.mock.calls.at(-1)![0] as (nodes: Node<FlowNodeData>[]) => Node<FlowNodeData>[];
    const cfg = update([node])[0]!.data.config!;
    expect((cfg.tools as McpFlowTool[]).map((t) => t.name)).toEqual(['list_issues', 'create_pr']);
  });
});

describe('McpFlowNode tool approvals', () => {
  function renderWithAgent(agentKey: string | null, readOnly = false) {
    const node = mcpNode({ allTools: false, tools: [LIST, CREATE] });
    const listed = entry({
      tools: [
        { name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {}, annotations: { readOnlyHint: true }, kind: 'read', kindSource: 'server' },
        { name: 'create_pr', namespacedName: 'mcp_github_create_pr', inputSchema: {}, kind: 'write', kindSource: 'none' },
      ],
    });
    renderInTheme(
      <McpLiveServersContext.Provider value={[listed]}>
        <McpAgentRulesContext.Provider value={agentKey}>
          <McpFlowNode id={node.id} data={node.data} selected={false} readOnly={readOnly} />
        </McpAgentRulesContext.Provider>
      </McpLiveServersContext.Provider>
    );
  }

  beforeEach(() => {
    mcpApi.getAgentToolRules.mockResolvedValue({ tools: {} });
  });

  it("opens the agent's rules for the tools it has, starting each from what it does to data", async () => {
    renderWithAgent('agent-1');
    fireEvent.click(screen.getByRole('button', { name: 'Tool approvals' }));

    expect(await screen.findByText(/^Tool approvals: github$/i)).toBeTruthy();
    expect(mcpApi.getAgentToolRules).toHaveBeenCalledWith('agent-1', 'inst-1');
    const listRow = within(await screen.findByRole('group', { name: 'list_issues' }));
    expect(listRow.getByRole('radio', { name: 'Pre-approved', checked: true })).toBeTruthy();
    expect(
      within(screen.getByRole('group', { name: 'create_pr' })).getByRole('radio', { name: 'Allow on approval', checked: true })
    ).toBeTruthy();
  });

  it('is offered only once the agent is saved', () => {
    renderWithAgent(null);
    expect(screen.queryByRole('button', { name: 'Tool approvals' })).toBeNull();
  });

  it("is offered only once the server's tools are known, so their hints are right", () => {
    const node = mcpNode({ allTools: false, tools: [LIST] });
    renderInTheme(
      <McpLiveServersContext.Provider value={null}>
        <McpAgentRulesContext.Provider value="agent-1">
          <McpFlowNode id={node.id} data={node.data} selected={false} />
        </McpAgentRulesContext.Provider>
      </McpLiveServersContext.Provider>
    );
    expect(screen.queryByRole('button', { name: 'Tool approvals' })).toBeNull();
  });

  it('shows the rules without controls to a viewer', async () => {
    renderWithAgent('agent-1', true);
    fireEvent.click(screen.getByRole('button', { name: 'Tool approvals' }));

    expect(await screen.findByText('Only people who can edit this agent can change these.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });
});

describe('saving', () => {
  const core = { id: 'core', data: { type: 'agent-core', config: {} } };
  const edge = { source: 'mcp-node', target: 'core', targetHandle: 'mcpServers' };

  it('sends allTools and leaves unknown full names for the server to fill in', () => {
    const node = mcpNode({ allTools: true, tools: [{ name: 'list_issues', fullName: '' }] });
    const payload = extractAgentConfigFromFlow('A', [core, node], [edge]);
    expect(payload.mcpServers).toEqual([
      expect.objectContaining({
        instanceId: 'inst-1',
        allTools: true,
        tools: [{ name: 'list_issues', fullName: '', description: '' }],
      }),
    ]);
  });

  it("leaves the MCP servers out when the server couldn't read them, so the save keeps them", () => {
    const loaded = { mcpServersUnavailable: true } as unknown as Parameters<typeof extractAgentConfigFromFlow>[3];
    const payload = extractAgentConfigFromFlow('A', [core], [], loaded);
    expect('mcpServers' in payload).toBe(false);
  });

  it('sends the MCP servers, even none, when they were read', () => {
    const loaded = { mcpServers: [] } as unknown as Parameters<typeof extractAgentConfigFromFlow>[3];
    expect(extractAgentConfigFromFlow('A', [core], [], loaded).mcpServers).toEqual([]);
  });

  it('names an attached server that has no tools and not all tools', () => {
    const empty = mcpNode({ allTools: false, tools: [] });
    const loose = mcpNode({ allTools: false, tools: [] }, 'loose');
    expect(findMcpServersWithoutTools([empty, loose], [edge])).toEqual(['github']);
    expect(findMcpServersWithoutTools([mcpNode({ allTools: true, tools: [] })], [edge])).toEqual([]);
  });
});

describe('AgentBuilderMcpSection', () => {
  it("won't let a personal server onto a shared agent", () => {
    const onNotify = vi.fn();
    renderInTheme(
      <AgentBuilderMcpSection
        mcpServers={[entry({ scope: 'personal', name: 'mine' })]}
        loading={false}
        refreshMcpServers={async () => {}}
        mcpMergeCheckNodes={[]}
        agentShared
        onNotify={onNotify}
      />,
    );
    const row = screen.getByText('mine').closest('[draggable]') as HTMLElement;
    fireEvent.dragStart(row, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } });
    expect(onNotify).toHaveBeenCalledWith(expect.stringContaining('mine is your personal MCP server'));
  });
});
