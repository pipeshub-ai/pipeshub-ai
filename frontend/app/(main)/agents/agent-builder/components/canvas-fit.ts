import type { Node } from '@xyflow/react';
import type { FlowNodeData } from '../types';

/** Whole-graph framing used on desktop and by the "fit" control. */
export const AGENT_BUILDER_FLOW_FIT = {
  padding: 0.08,
  minZoom: 0.25,
  maxZoom: 1.38,
  duration: 0,
} as const;

/**
 * At phone width the whole graph fits only at the minimum zoom, where nothing is readable and the
 * agent node can end up clipped. Frame the agent node alone and let the user pan to the rest.
 */
export function initialFitOptions(nodes: Node<FlowNodeData>[], isMobile: boolean) {
  if (!isMobile) return { ...AGENT_BUILDER_FLOW_FIT };
  const agent = nodes.find((n) => n.data?.type === 'agent-core');
  if (!agent) return { ...AGENT_BUILDER_FLOW_FIT };
  return { ...AGENT_BUILDER_FLOW_FIT, nodes: [{ id: agent.id }], padding: 0.1, maxZoom: 1 };
}
