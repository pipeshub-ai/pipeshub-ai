import type { Connection, Edge, Node } from '@xyflow/react';
import type { FlowNodeData } from './types';
import { FLOW_EDGE } from './flow-theme';

/** i18n keys under `agentBuilder.*` — translate where shown to the user. */
export function connectionError(
  sourceNode: Node<FlowNodeData> | undefined,
  targetNode: Node<FlowNodeData> | undefined,
  connection: Connection
): string | null {
  if (!sourceNode || !targetNode) return 'agentBuilder.connectionInvalid';
  const st = sourceNode.data?.type ?? '';
  const tt = targetNode.data?.type ?? '';

  if (
    (st === 'kb-group' || st === 'app-group') &&
    (tt !== 'agent-core' || connection.targetHandle !== 'knowledge')
  ) {
    return 'agentBuilder.connectionGroupKnowledge';
  }
  if (st.startsWith('kb-') && st !== 'kb-group' && (tt !== 'agent-core' || connection.targetHandle !== 'knowledge')) {
    return 'agentBuilder.connectionKbToKnowledgeHandle';
  }
  if (st.startsWith('app-') && st !== 'app-group' && (tt !== 'agent-core' || connection.targetHandle !== 'knowledge')) {
    return 'agentBuilder.connectionAppToKnowledgeHandle';
  }
  if (st.startsWith('llm-') && (tt !== 'agent-core' || connection.targetHandle !== 'llms')) {
    return 'agentBuilder.connectionLlmToModelsHandle';
  }
  if (st === 'user-input' && (tt !== 'agent-core' || connection.targetHandle !== 'input')) {
    return 'agentBuilder.connectionChatInputToInput';
  }
  if (st.startsWith('tool-group-') && (tt !== 'agent-core' || connection.targetHandle !== 'toolsets')) {
    return 'agentBuilder.connectionToolGroupToToolsets';
  }
  if (st.startsWith('toolset-') && (tt !== 'agent-core' || connection.targetHandle !== 'toolsets')) {
    return 'agentBuilder.connectionToolsetToToolsets';
  }
  if (st === 'web-search' && (tt !== 'agent-core' || connection.targetHandle !== 'toolsets')) {
    return 'agentBuilder.connectionWebSearchToToolsets';
  }
  if (
    st.startsWith('tool-') &&
    !st.startsWith('tool-group-') &&
    (tt !== 'agent-core' || connection.targetHandle !== 'toolsets')
  ) {
    return 'agentBuilder.connectionToolToToolsets';
  }
  if (st === 'agent-core') {
    if (tt !== 'chat-response' || connection.sourceHandle !== 'response') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }
  if (st !== 'agent-core' && tt !== 'agent-core' && tt !== 'chat-response') {
    return 'agentBuilder.connectionThroughAgent';
  }
  return null;
}

function edgeStyle(): { stroke: string; strokeWidth: number } {
  return { stroke: FLOW_EDGE.line, strokeWidth: 1.5 };
}

function isChatResponseTarget(nodes: Node<FlowNodeData>[], edge: Edge): boolean {
  const t = nodes.find((n) => n.id === edge.target);
  return t?.data?.type === 'chat-response';
}

/**
 * After a new node is dropped, wire it to the agent (or wire agent to chat output) using the same rules as manual connect.
 */
export function applyAutoConnectToEdges(
  droppedNode: Node<FlowNodeData>,
  allNodes: Node<FlowNodeData>[],
  currentEdges: Edge[]
): Edge[] {
  const agent = allNodes.find((n) => n.data?.type === 'agent-core');
  if (!agent) return currentEdges;

  const st = droppedNode.data.type ?? '';
  const outs = droppedNode.data.outputs ?? [];

  let working = currentEdges;

  if (st === 'chat-response') {
    const connection: Connection = {
      source: agent.id,
      target: droppedNode.id,
      sourceHandle: 'response',
      targetHandle: 'response',
    };
    if (connectionError(agent, droppedNode, connection)) return currentEdges;
    working = working.filter(
      (e) =>
        !(
          e.source === agent.id &&
          e.sourceHandle === 'response' &&
          isChatResponseTarget(allNodes, e)
        )
    );
    const exists = working.some(
      (e) =>
        e.source === connection.source &&
        e.target === connection.target &&
        e.sourceHandle === connection.sourceHandle &&
        e.targetHandle === connection.targetHandle
    );
    if (exists) return working;
    return [
      ...working,
      {
        id: `e-${agent.id}-${droppedNode.id}-response-${Date.now()}`,
        ...connection,
        type: 'smoothstep',
        style: edgeStyle(),
      },
    ];
  }

  const proposals: Connection[] = [];

  if (st === 'user-input') {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'message',
      targetHandle: 'input',
    });
  } else if (st.startsWith('llm-')) {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'response',
      targetHandle: 'llms',
    });
  } else if (
    st === 'kb-group' ||
    (st.startsWith('kb-') && st !== 'kb-group') ||
    st === 'app-group' ||
    (st.startsWith('app-') && st !== 'app-group')
  ) {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'context',
      targetHandle: 'knowledge',
    });
  } else if (st.startsWith('toolset-')) {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'output',
      targetHandle: 'toolsets',
    });
  } else if (st.startsWith('tool-group-') || (st.startsWith('tool-') && !st.startsWith('tool-group-'))) {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'output',
      targetHandle: 'toolsets',
    });
  } else if (st === 'web-search') {
    proposals.push({
      source: droppedNode.id,
      target: agent.id,
      sourceHandle: 'results',
      targetHandle: 'toolsets',
    });
  } else if (st.startsWith('connector-group-')) {
    if (outs.includes('context')) {
      proposals.push({
        source: droppedNode.id,
        target: agent.id,
        sourceHandle: 'context',
        targetHandle: 'knowledge',
      });
    }
    if (outs.includes('actions')) {
      proposals.push({
        source: droppedNode.id,
        target: agent.id,
        sourceHandle: 'actions',
        targetHandle: 'toolsets',
      });
    }
  }

  if (proposals.length === 0) return currentEdges;

  if (st === 'user-input') {
    working = working.filter((e) => !(e.target === agent.id && e.targetHandle === 'input'));
  }

  let next = working;
  let tick = Date.now();
  for (const c of proposals) {
    if (connectionError(droppedNode, agent, c)) continue;
    const exists = next.some(
      (e) =>
        e.source === c.source &&
        e.target === c.target &&
        e.sourceHandle === c.sourceHandle &&
        e.targetHandle === c.targetHandle
    );
    if (exists) continue;
    next = [
      ...next,
      {
        id: `e-${c.source}-${c.target}-${c.targetHandle}-${tick++}`,
        source: c.source!,
        target: c.target!,
        sourceHandle: c.sourceHandle,
        targetHandle: c.targetHandle,
        type: 'smoothstep',
        style: edgeStyle(),
      },
    ];
  }

  return next;
}
