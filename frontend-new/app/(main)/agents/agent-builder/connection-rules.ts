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
  if (
    st.startsWith('kb-') &&
    st !== 'kb-group' &&
    (tt !== 'agent-core' || connection.targetHandle !== 'knowledge')
  ) {
    return 'agentBuilder.connectionKbToKnowledgeHandle';
  }
  if (
    st.startsWith('app-') &&
    st !== 'app-group' &&
    (tt !== 'agent-core' || connection.targetHandle !== 'knowledge')
  ) {
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

  // Linear chaining guardrails for agent->agent connections
  if (st === 'agent-core' && tt === 'agent-core') {
    if (connection.sourceHandle !== 'response' || connection.targetHandle !== 'input') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }

  // Agent->condition connections
  if (st === 'agent-core' && tt === 'conditional-check') {
    if (connection.sourceHandle !== 'response' || connection.targetHandle !== 'input') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }

  // Condition block input validation
  if (tt === 'conditional-check') {
    if (st !== 'agent-core') {
      return 'agentBuilder.connectionConditionInputOnly';
    }
    if (connection.targetHandle !== 'input' || connection.sourceHandle !== 'response') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }

  // Condition block output validation
  if (st === 'conditional-check') {
    if (connection.sourceHandle !== 'pass' && connection.sourceHandle !== 'fail') {
      return 'agentBuilder.connectionConditionPassFail';
    }
    if (tt !== 'agent-core' && tt !== 'chat-response') {
      return 'agentBuilder.connectionConditionToAgent';
    }
    if (tt === 'agent-core' && connection.targetHandle !== 'input') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
    if (tt === 'chat-response' && connection.targetHandle !== 'response') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }

  // Normal agent-core logic
  if (st === 'agent-core') {
    if (tt === 'chat-response') {
      if (connection.sourceHandle !== 'response' || connection.targetHandle !== 'response') {
        return 'agentBuilder.connectionAgentResponseToOutput';
      }
    } else if (tt !== 'agent-core' && tt !== 'conditional-check') {
      return 'agentBuilder.connectionAgentResponseToOutput';
    }
  }

  // Prevent invalid connections between non-agent nodes
  if (
    st !== 'agent-core' &&
    st !== 'conditional-check' &&
    tt !== 'agent-core' &&
    tt !== 'chat-response' &&
    tt !== 'conditional-check'
  ) {
    return 'agentBuilder.connectionThroughAgent';
  }

  // Condition blocks cannot connect to other condition blocks directly
  if (st === 'conditional-check' && tt === 'conditional-check') {
    return 'agentBuilder.connectionConditionToCondition';
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

function findChainAnchors(nodes: Node<FlowNodeData>[], edges: Edge[]): {
  firstAgent?: Node<FlowNodeData>;
  lastAgent?: Node<FlowNodeData>;
} {
  const agents = nodes.filter((n) => n.data?.type === 'agent-core');
  if (agents.length === 0) return {};

  const incomingAgent = new Set<string>();
  const outgoingAgent = new Set<string>();
  for (const edge of edges) {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    if (sourceNode?.data?.type === 'agent-core' && targetNode?.data?.type === 'agent-core') {
      outgoingAgent.add(sourceNode.id);
      incomingAgent.add(targetNode.id);
    }
  }

  const firstAgent = agents.find((n) => !incomingAgent.has(n.id)) || agents[0];
  const lastAgent = agents.find((n) => !outgoingAgent.has(n.id)) || agents[agents.length - 1];
  return { firstAgent, lastAgent };
}

/**
 * After a new node is dropped, wire it to the linear chain
 * (first agent as input anchor, last agent as output anchor).
 */
export function applyAutoConnectToEdges(
  droppedNode: Node<FlowNodeData>,
  allNodes: Node<FlowNodeData>[],
  currentEdges: Edge[]
): Edge[] {
  const { firstAgent, lastAgent } = findChainAnchors(allNodes, currentEdges);
  const primaryAgent = firstAgent || lastAgent;
  if (!primaryAgent) return currentEdges;

  const st = droppedNode.data.type ?? '';
  const outs = droppedNode.data.outputs ?? [];

  let working = currentEdges;

  if (st === 'agent-core') {
    if (!lastAgent || lastAgent.id === droppedNode.id) return currentEdges;

    const chainConnection: Connection = {
      source: lastAgent.id,
      target: droppedNode.id,
      sourceHandle: 'response',
      targetHandle: 'input',
    };
    if (!connectionError(lastAgent, droppedNode, chainConnection)) {
      const exists = working.some(
        (e) =>
          e.source === chainConnection.source &&
          e.target === chainConnection.target &&
          e.sourceHandle === chainConnection.sourceHandle &&
          e.targetHandle === chainConnection.targetHandle
      );
      if (!exists) {
        working = [
          ...working,
          {
            id: `e-${lastAgent.id}-${droppedNode.id}-input-${Date.now()}`,
            ...chainConnection,
            type: 'smoothstep',
            style: edgeStyle(),
          },
        ];
      }
    }

    const outputEdges = working.filter(
      (e) =>
        e.source === lastAgent.id &&
        e.sourceHandle === 'response' &&
        isChatResponseTarget(allNodes, e)
    );
    if (outputEdges.length > 0) {
      const removedIds = new Set(outputEdges.map((e) => e.id));
      const tick = Date.now();
      const rewired = outputEdges.map((edge, idx) => ({
        ...edge,
        id: `e-${droppedNode.id}-${edge.target}-response-${tick + idx}`,
        source: droppedNode.id,
        sourceHandle: 'response',
        targetHandle: 'response',
      }));
      working = [...working.filter((e) => !removedIds.has(e.id)), ...rewired];
    }

    return working;
  }

  if (st === 'chat-response') {
    const sourceAgent = lastAgent || primaryAgent;
    const connection: Connection = {
      source: sourceAgent.id,
      target: droppedNode.id,
      sourceHandle: 'response',
      targetHandle: 'response',
    };
    if (connectionError(sourceAgent, droppedNode, connection)) return currentEdges;
    working = working.filter(
      (e) =>
        !(
          e.source === sourceAgent.id &&
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
        id: `e-${sourceAgent.id}-${droppedNode.id}-response-${Date.now()}`,
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
      target: primaryAgent.id,
      sourceHandle: 'message',
      targetHandle: 'input',
    });
  } else if (st.startsWith('llm-')) {
    proposals.push({
      source: droppedNode.id,
      target: primaryAgent.id,
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
      target: primaryAgent.id,
      sourceHandle: 'context',
      targetHandle: 'knowledge',
    });
  } else if (st.startsWith('toolset-')) {
    proposals.push({
      source: droppedNode.id,
      target: primaryAgent.id,
      sourceHandle: 'output',
      targetHandle: 'toolsets',
    });
  } else if (st.startsWith('tool-group-') || (st.startsWith('tool-') && !st.startsWith('tool-group-'))) {
    proposals.push({
      source: droppedNode.id,
      target: primaryAgent.id,
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
        target: primaryAgent.id,
        sourceHandle: 'context',
        targetHandle: 'knowledge',
      });
    }
    if (outs.includes('actions')) {
      proposals.push({
        source: droppedNode.id,
        target: primaryAgent.id,
        sourceHandle: 'actions',
        targetHandle: 'toolsets',
      });
    }
  }

  if (proposals.length === 0) return currentEdges;

  if (st === 'user-input') {
    working = working.filter((e) => !(e.target === primaryAgent.id && e.targetHandle === 'input'));
  }

  let next = working;
  let tick = Date.now();
  for (const c of proposals) {
    const targetNode = allNodes.find((n) => n.id === c.target);
    if (connectionError(droppedNode, targetNode, c)) continue;
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