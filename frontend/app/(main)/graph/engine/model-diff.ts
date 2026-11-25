import type { ElementDefinition } from 'cytoscape';
import type { GraphModel } from '../model/graph-model';
import { CELL_HEIGHT, CELL_WIDTH } from '../model/placement';

/**
 * The model as the picture is allowed to show it: the first `cap` nodes (in the order they arrived, so
 * what is already drawn stays drawn) and the edges between them. The table lists everything.
 */
export function capModel(model: GraphModel, cap: number): GraphModel {
  const ids = Object.keys(model.nodes);
  if (ids.length <= cap) return model;
  const keep = new Set(ids.slice(0, cap));
  return {
    ...model,
    nodes: Object.fromEntries(ids.slice(0, cap).map((id) => [id, model.nodes[id]])),
    edges: Object.fromEntries(Object.entries(model.edges).filter(([, e]) => keep.has(e.from) && keep.has(e.to))),
  };
}

export interface ModelDiff {
  addNodes: ElementDefinition[];
  addEdges: ElementDefinition[];
  removeIds: string[];
  /** Nodes whose label or kind changed (a fuller record arrived). */
  updateNodes: { id: string; data: Record<string, unknown> }[];
  /** Nodes drawn before that sit on a different cell in a new view: the layout is new, so they move with it. */
  moveNodes: { id: string; position: { x: number; y: number } }[];
}

const nodeData = (model: GraphModel, id: string) => {
  const n = model.nodes[id];
  return { id, label: n.label ?? id, kind: n.kind ?? '', file: n.file ?? '', line: n.line };
};

/** What to add, remove and update to turn what was drawn into `next`. */
export function diffModel(prev: GraphModel | null, next: GraphModel): ModelDiff {
  const before = prev ?? { nodes: {}, edges: {}, positions: {}, view: next.view };
  const diff: ModelDiff = { addNodes: [], addEdges: [], removeIds: [], updateNodes: [], moveNodes: [] };
  const relaid = before.view !== next.view;
  for (const id of Object.keys(before.nodes)) if (!next.nodes[id]) diff.removeIds.push(id);
  for (const id of Object.keys(before.edges)) if (!next.edges[id]) diff.removeIds.push(id);
  for (const id of Object.keys(next.nodes)) {
    const cell = next.positions[id] ?? { c: 0, r: 0 };
    if (!before.nodes[id]) {
      diff.addNodes.push({
        group: 'nodes',
        data: nodeData(next, id),
        position: { x: cell.c * CELL_WIDTH, y: cell.r * CELL_HEIGHT },
      });
    } else {
      const was = before.nodes[id];
      const now = next.nodes[id];
      if (was.label !== now.label || was.kind !== now.kind) diff.updateNodes.push({ id, data: nodeData(next, id) });
      // Within a view a node stays where the person may have dragged it; a new view is placed afresh.
      if (relaid) diff.moveNodes.push({ id, position: { x: cell.c * CELL_WIDTH, y: cell.r * CELL_HEIGHT } });
    }
  }
  for (const [id, e] of Object.entries(next.edges)) {
    // An edge whose end is not drawn cannot be added; it waits for the next model.
    if (before.edges[id] || !next.nodes[e.from] || !next.nodes[e.to]) continue;
    diff.addEdges.push({
      group: 'edges',
      data: { id, source: e.from, target: e.to, type: e.type, inferred: e.confidence === 'INFERRED' ? 1 : 0 },
    });
  }
  return diff;
}
