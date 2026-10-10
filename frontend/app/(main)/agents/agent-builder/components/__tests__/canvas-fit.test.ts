import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import type { FlowNodeData } from '../../types';
import { AGENT_BUILDER_FLOW_FIT, initialFitOptions } from '../canvas-fit';

const node = (id: string, type: string) =>
  ({ id, position: { x: 0, y: 0 }, data: { type } }) as unknown as Node<FlowNodeData>;

describe('initialFitOptions', () => {
  const nodes = [node('in', 'chat-input'), node('agent-1', 'agent-core'), node('out', 'chat-output')];

  it('fits the whole graph on desktop', () => {
    expect(initialFitOptions(nodes, false)).toEqual(AGENT_BUILDER_FLOW_FIT);
  });

  it('frames only the agent node on mobile, capped at 1x zoom', () => {
    const o = initialFitOptions(nodes, true);
    expect(o.nodes).toEqual([{ id: 'agent-1' }]);
    expect(o.maxZoom).toBe(1);
  });

  it('falls back to the whole graph on mobile when there is no agent node', () => {
    expect(initialFitOptions([node('in', 'chat-input')], true)).toEqual(AGENT_BUILDER_FLOW_FIT);
  });
});
