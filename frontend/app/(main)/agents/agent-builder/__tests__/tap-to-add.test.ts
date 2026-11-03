import { describe, it, expect, vi } from 'vitest';
import type { Node } from '@xyflow/react';
import type { TFunction } from 'i18next';
import type { FlowNodeData } from '../types';
import { addNodeAtViewportCenter, buildTapDropPayload } from '../components/canvas-drop-handler';
import { buildToolsetDragPayload } from '../sidebar-toolset-utils';
import { resolvePremiumDropPosition } from '../drop-position';
import { toolset } from './agent-builder-harness';

const agentCore: Node<FlowNodeData> = {
  id: 'agent-core-1',
  type: 'flowNode',
  position: { x: 420, y: 120 },
  data: {
    id: 'agent-core-1',
    type: 'agent-core',
    label: 'Agent',
    config: {},
    inputs: ['input', 'toolsets', 'knowledge', 'llms', 'skills', 'mcpServers'],
    outputs: ['response'],
  },
};

describe('tap to add', () => {
  it('reads the same fields a drag would carry', () => {
    const payload = buildTapDropPayload({ 'application/reactflow': 'toolset-jira' });
    expect(payload.getData('application/reactflow')).toBe('toolset-jira');
    expect(payload.getData('missing')).toBe('');
  });

  it('places a toolset at the centre of the canvas and wires it to the agent', () => {
    const screenToFlowPosition = vi.fn((p: { x: number; y: number }) => ({ x: p.x + 1000, y: p.y + 1000 }));
    const canvas = { getBoundingClientRect: () => ({ left: 0, top: 100, width: 400, height: 600 }) } as Element;
    const nodes = [agentCore];

    const result = addNodeAtViewportCenter({
      data: buildToolsetDragPayload(toolset()),
      reactFlow: { screenToFlowPosition },
      canvas,
      nodes,
      edges: [],
      drop: {
        nodeTemplates: [],
        configuredConnectors: [],
        activeAgentConnectors: [],
        readOnly: false,
        t: ((k: string) => k) as unknown as TFunction,
      },
    });

    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 200, y: 400 });
    expect(result.nodes).toHaveLength(2);
    const added = result.nodes[1];
    expect(added.data.type).toBe('toolset-jira');
    expect(added.position).toEqual(resolvePremiumDropPosition({ x: 1200, y: 1400 }, 'toolset-jira', nodes));
    expect(result.edges).toEqual([
      expect.objectContaining({ source: added.id, target: 'agent-core-1', targetHandle: 'toolsets' }),
    ]);
  });
});
