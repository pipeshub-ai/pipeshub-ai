import { describe, it, expect } from 'vitest';
import type { Connection, Edge, Node } from '@xyflow/react';
import i18n from '@/lib/__tests__/test-i18n';
import type { FlowNodeData } from '../types';
import { applyAutoConnectToEdges, connectionError } from '../connection-rules';

function node(id: string, type: string, outputs: string[] = []): Node<FlowNodeData> {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { id, type, label: id, description: '', icon: '', config: {}, inputs: [], outputs },
  };
}

const agent = node('agent-core-1', 'agent-core');
const chatOutput = node('chat-response-1', 'chat-response');

function explain(source: Node<FlowNodeData>, target: Node<FlowNodeData>, handles: Partial<Connection> = {}) {
  const key = connectionError(source, target, {
    source: source.id,
    target: target.id,
    sourceHandle: handles.sourceHandle ?? null,
    targetHandle: handles.targetHandle ?? null,
  });
  return key ? i18n.t(key) : null;
}

describe('connection rules', () => {
  it.each([
    ['a model', node('m', 'llm-openai-gpt'), 'knowledge', 'Model nodes must connect to the agent models handle'],
    ['a collection', node('k', 'kb-1'), 'llms', 'Knowledge nodes must connect to the agent knowledge handle'],
    ['an app', node('a', 'app-drive'), 'toolsets', 'App nodes must connect to the agent knowledge handle'],
    ['the collections group', node('g', 'kb-group'), 'input', 'Group nodes must connect to the agent knowledge handle'],
    ['a toolset', node('t', 'toolset-jira'), 'knowledge', 'Toolsets must connect to the agent toolsets handle'],
    ['a skill', node('s', 'skill-pdf'), 'toolsets', 'Skills must connect to the agent skills handle'],
    ['the chat input', node('i', 'user-input'), 'llms', 'Connect chat input to the agent input handle'],
  ])('explains where %s has to go when it is dropped on the wrong handle', (_what, source, handle, message) => {
    expect(explain(source, agent, { targetHandle: handle })).toBe(message);
  });

  it('accepts the connections the agent expects', () => {
    expect(explain(node('m', 'llm-openai-gpt'), agent, { targetHandle: 'llms' })).toBeNull();
    expect(explain(node('k', 'kb-1'), agent, { targetHandle: 'knowledge' })).toBeNull();
    expect(explain(node('t', 'toolset-jira'), agent, { targetHandle: 'toolsets' })).toBeNull();
    expect(explain(agent, chatOutput, { sourceHandle: 'response' })).toBeNull();
  });

  it('requires nodes to connect through the agent rather than to each other', () => {
    expect(explain(agent, node('x', 'kb-1'), { sourceHandle: 'response' })).toBe(
      'Connect the agent response handle to chat output',
    );
    expect(explain(node('k', 'kb-1'), node('m', 'llm-openai-gpt'), { targetHandle: 'knowledge' })).toBe(
      'Knowledge nodes must connect to the agent knowledge handle',
    );
  });

  it('wires a dropped node to the matching agent handle, once', () => {
    const model = node('llm-1', 'llm-openai-gpt');
    const first = applyAutoConnectToEdges(model, [agent, model], []);
    expect(first).toEqual([expect.objectContaining({ source: 'llm-1', target: 'agent-core-1', targetHandle: 'llms' })]);
    expect(applyAutoConnectToEdges(model, [agent, model], first)).toBe(first);
  });

  it('replaces the old chat input link when a new chat input is dropped', () => {
    const oldInput: Edge = { id: 'old', source: 'input-old', target: 'agent-core-1', targetHandle: 'input' };
    const input = node('input-new', 'user-input');
    const edges = applyAutoConnectToEdges(input, [agent, input], [oldInput]);
    expect(edges.map((e) => e.source)).toEqual(['input-new']);
  });

  it('leaves the edges alone when there is no agent on the canvas', () => {
    const model = node('llm-1', 'llm-openai-gpt');
    const edges: Edge[] = [];
    expect(applyAutoConnectToEdges(model, [model], edges)).toBe(edges);
  });
});
