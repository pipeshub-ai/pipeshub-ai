import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { FlowNodeData } from '../../types';

const flow = vi.hoisted(() => ({
  nodes: [] as { id: string; data: unknown }[],
  edges: [] as { source: string; target: string; targetHandle?: string }[],
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useReactFlow: () => ({ setNodes: vi.fn() }),
  useStore: (selector: (s: typeof flow) => unknown) => selector(flow),
  useNodeConnections: () => [],
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} />,
}));

import { AgentCoreNode } from '../agent-core-node';

const AGENT: FlowNodeData = {
  id: 'agent-core',
  type: 'agent-core',
  label: 'Agent',
  description: '',
  icon: 'smart_toy',
  category: 'agent',
  config: {},
  inputs: [],
  outputs: [],
  isConfigured: true,
};

function mcpServer(instanceId: string, name: string, typeId?: string): FlowNodeData {
  return {
    id: `mcp-${instanceId}-1`,
    type: `mcp-${instanceId}`,
    label: name,
    description: '',
    icon: 'hub',
    category: 'mcp-server',
    config: { instanceId, name, displayName: name, typeId, allTools: true, tools: [] },
    inputs: [],
    outputs: ['output'],
    isConfigured: true,
  };
}

function renderWithServers(servers: FlowNodeData[]) {
  flow.nodes = servers.map((s) => ({ id: s.id, data: s }));
  flow.edges = servers.map((s) => ({ source: s.id, target: AGENT.id, targetHandle: 'mcpServers' }));
  return render(
    <Theme>
      <AgentCoreNode data={AGENT} selected={false} />
    </Theme>,
  );
}

const chip = (label: string) => screen.getByTitle(label);

afterEach(() => {
  cleanup();
  flow.nodes = [];
  flow.edges = [];
});

describe('AgentCoreNode MCP servers section', () => {
  it("shows each attached server's own logo", () => {
    renderWithServers([mcpServer('exa-1', 'Exa', 'exa'), mcpServer('slack-1', 'Slack', 'slack')]);

    expect(within(chip('Exa')).getByRole('img', { name: 'exa icon' })).toBeTruthy();
    expect(within(chip('Slack')).getByRole('img', { name: 'slack icon' })).toBeTruthy();
  });

  it('keeps the MCP mark for a custom server with no logo, not a stock picture', () => {
    renderWithServers([mcpServer('own-1', 'Acme internal')]);

    expect(within(chip('Acme internal')).queryByRole('img')).toBeNull();
    expect(chip('Acme internal').querySelector('[data-icon="hub"]')).toBeTruthy();
  });
});
