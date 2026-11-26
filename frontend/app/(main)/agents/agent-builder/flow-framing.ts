export interface FlowRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface FlowViewport {
  x: number;
  y: number;
  zoom: number;
}

/**
 * How the editor first frames an agent. `readableZoom` is the smallest zoom the card's 12px text stays legible at
 * (9px); fitting a tall card whole would go far below it.
 */
export const INITIAL_FLOW_FRAMING = {
  padding: 0.08,
  readableZoom: 0.75,
  maxZoom: 1.38,
} as const;

/**
 * The viewport the editor opens at: the whole flow, centered, when it fits at a readable zoom; otherwise the readable
 * zoom with the agent card's top at the top of the canvas, centered on the card, so its first sections (the model,
 * the coding engine) are in view and the rest is a pan away. Null until the canvas has a size.
 */
export function initialFlowViewport({
  graph,
  focus,
  width,
  height,
}: {
  graph: FlowRect;
  focus: FlowRect | null;
  width: number;
  height: number;
}): FlowViewport | null {
  if (!(width > 0) || !(height > 0)) return null;
  const { padding, readableZoom, maxZoom } = INITIAL_FLOW_FRAMING;
  const fit = Math.min(
    graph.width > 0 ? (width * (1 - 2 * padding)) / graph.width : maxZoom,
    graph.height > 0 ? (height * (1 - 2 * padding)) / graph.height : maxZoom,
  );
  if (fit >= readableZoom) {
    const zoom = Math.min(fit, maxZoom);
    return {
      x: width / 2 - (graph.x + graph.width / 2) * zoom,
      y: height / 2 - (graph.y + graph.height / 2) * zoom,
      zoom,
    };
  }
  const card = focus ?? graph;
  return {
    x: width / 2 - (card.x + card.width / 2) * readableZoom,
    y: height * padding - card.y * readableZoom,
    zoom: readableZoom,
  };
}
