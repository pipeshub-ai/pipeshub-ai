/**
 * P2.2 DN-4: an agent's model node is labelled with its display name. The catalog may not
 * know the name (an entry listing several models, or the model was removed); the agents
 * API has already resolved one, and the label uses it before falling back to the raw model.
 */
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAgentBuilderReconstruction } from '../hooks/use-flow-reconstruction';
import type { AgentReconstructionSource } from '../types';

const agent = (models: unknown[]): AgentReconstructionSource =>
  ({
    _key: 'agent-1',
    name: 'Agent',
    description: '',
    startMessage: '',
    systemPrompt: '',
    models,
    toolsets: [],
    knowledge: [],
    tools: [],
    kb: [],
    connectors: [],
  }) as unknown as AgentReconstructionSource;

const llmLabels = (models: unknown[], catalog: unknown[]): string[] => {
  const { result } = renderHook(() => useAgentBuilderReconstruction());
  const { nodes } = result.current.reconstructFlowFromAgent(agent(models), catalog, [], []);
  return nodes.filter((n) => n.id.startsWith('llm-')).map((n) => String(n.data.label));
};

describe('DN-4 agent builder model label', () => {
  it('uses the display name the agents API resolved when the catalog has none', () => {
    const labels = llmLabels(
      [{ modelKey: 'k1', provider: 'openAI', modelName: 'gpt-4o-mini', modelFriendlyName: 'Team GPT (gpt-4o-mini)' }],
      [{ modelKey: 'k1', provider: 'openAI', modelName: 'gpt-4o-mini' }],
    );
    expect(labels).toEqual(['Team GPT (gpt-4o-mini)']);
  });

  it("prefers the catalog's display name, and never shows the model key", () => {
    const labels = llmLabels(
      [{ modelKey: 'k1', provider: 'openAI', modelName: 'gpt-4o', modelFriendlyName: 'gpt-4o' }],
      [{ modelKey: 'k1', provider: 'openAI', modelName: 'gpt-4o', modelFriendlyName: 'Team GPT' }],
    );
    expect(labels).toEqual(['Team GPT']);
    expect(labels.join()).not.toContain('k1');
  });
});
