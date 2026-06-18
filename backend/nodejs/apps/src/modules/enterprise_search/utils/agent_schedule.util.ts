export interface AgentScheduledTrigger {
  triggerId: string;
  cronExpression: string;
  timezone: string;
  input: string;
}

type FlowNode = {
  id?: string;
  data?: {
    type?: string;
    config?: Record<string, unknown>;
  };
};

type FlowEdge = {
  source?: string;
  target?: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
};

const CRON_5_FIELD_REGEX = /^(\S+\s+){4}\S+$/;

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function extractScheduledTriggersFromFlow(flow: unknown): AgentScheduledTrigger[] {
  if (!flow || typeof flow !== 'object') return [];

  const flowObj = flow as { nodes?: unknown; edges?: unknown };
  const nodes = asArray<FlowNode>(flowObj.nodes);
  const edges = asArray<FlowEdge>(flowObj.edges);

  const nodesById = new Map<string, FlowNode>();
  for (const node of nodes) {
    if (typeof node?.id === 'string' && node.id.trim()) {
      nodesById.set(node.id, node);
    }
  }

  const connectedScheduledNodeIds = new Set<string>();
  for (const edge of edges) {
    const sourceId = typeof edge?.source === 'string' ? edge.source : '';
    const targetId = typeof edge?.target === 'string' ? edge.target : '';
    if (!sourceId || !targetId) continue;

    const sourceNodeType = nodesById.get(sourceId)?.data?.type ?? '';
    const targetNodeType = nodesById.get(targetId)?.data?.type ?? '';
    const targetHandle = typeof edge?.targetHandle === 'string' ? edge.targetHandle : null;

    if (
      sourceNodeType === 'scheduled-input' &&
      targetNodeType === 'agent-core' &&
      targetHandle === 'input'
    ) {
      connectedScheduledNodeIds.add(sourceId);
    }
  }

  const triggers: AgentScheduledTrigger[] = [];
  for (const nodeId of connectedScheduledNodeIds) {
    const node = nodesById.get(nodeId);
    const config = (node?.data?.config ?? {}) as Record<string, unknown>;

    const enabled = Boolean(config.enabled ?? true);
    if (!enabled) continue;

    const cronExpression = String(config.cronExpression ?? '').trim();
    const timezone = String(config.timezone ?? '').trim() || 'UTC';
    const input = String(config.input ?? '').trim();

    if (!cronExpression || !CRON_5_FIELD_REGEX.test(cronExpression)) continue;
    if (!input) continue;

    triggers.push({
      triggerId: nodeId,
      cronExpression,
      timezone,
      input,
    });
  }

  return triggers;
}
