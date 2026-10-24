/** Horizontal distance between two layer columns of a reconstructed agent flow. */
export const FLOW_LAYER_STEP = 450;
const FLOW_LAYER_START_X = 100;

/**
 * X of each layer column, left to right. A layer with no nodes takes no column,
 * so a basic agent spans three columns and the first fit stays readable.
 */
export function flowLayerX(counts: { knowledge: number; llm: number; tools: number }) {
  let x = FLOW_LAYER_START_X;
  const column = (count = 1) => {
    const at = x;
    if (count > 0) x += FLOW_LAYER_STEP;
    return at;
  };
  return {
    input: column(),
    knowledge: column(counts.knowledge),
    llm: column(counts.llm),
    tools: column(counts.tools),
    agent: column(),
    output: column(),
  };
}
