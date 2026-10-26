import { describe, it, expect } from 'vitest';
import { FLOW_LAYER_STEP, flowLayerX } from '../flow-layout';

describe('flowLayerX', () => {
  it('places a basic agent in three columns: input, agent, output', () => {
    const x = flowLayerX({ knowledge: 0, llm: 0, tools: 0 });
    expect(x.agent - x.input).toBe(FLOW_LAYER_STEP);
    expect(x.output - x.agent).toBe(FLOW_LAYER_STEP);
  });

  it('gives an empty layer no width', () => {
    const withKnowledge = flowLayerX({ knowledge: 2, llm: 1, tools: 0 });
    const without = flowLayerX({ knowledge: 0, llm: 1, tools: 0 });
    expect(withKnowledge.agent - without.agent).toBe(FLOW_LAYER_STEP);
    expect(without.llm).toBe(without.input + FLOW_LAYER_STEP);
  });

  it('keeps every layer in its own column, left to right, when all are filled', () => {
    const x = flowLayerX({ knowledge: 1, llm: 1, tools: 3 });
    const order = [x.input, x.knowledge, x.llm, x.tools, x.agent, x.output];
    order.slice(1).forEach((value, i) => expect(value - order[i]).toBe(FLOW_LAYER_STEP));
  });
});
