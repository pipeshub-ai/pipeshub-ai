import type {
  ExpandRequest,
  ExpandResponse,
  GraphEdge,
  GraphNode,
  GraphStatus,
  NodeId,
} from '../types';
import { cellKey, placeNodes, type Cell, type Side } from './placement';

/**
 * What is on screen, as plain data the renderer draws from. It lives apart from
 * the renderer and from the store (the store holds selection and filters, never
 * nodes). Every function returns a new model.
 */
export interface GraphModel {
  /** Which view this is: a new view (Open, a filter change, a link) gets a new number, so the picture knows to fit it. */
  view: number;
  nodes: Record<NodeId, GraphNode>;
  edges: Record<string, GraphEdge>;
  positions: Record<NodeId, Cell>;
  /** Exact totals only. A truncated read has none: a number equal to the page size is noise. */
  totals: Record<NodeId, number>;
  /** Nodes that have been expanded: what the table may say about their links. A node never expanded has an unknown number, not zero. */
  expanded: Record<NodeId, true>;
  /** Anchors with more to show, and the request that continues them. */
  more: Record<NodeId, { group: NodeId[]; cursor: string }>;
  /** Some links may be missing (the scan budget ran out). Sticky until the model is reset. */
  incomplete: boolean;
  status: GraphStatus;
}

export const emptyModel = (): GraphModel => ({
  view: 0,
  nodes: {},
  edges: {},
  positions: {},
  totals: {},
  expanded: {},
  more: {},
  incomplete: false,
  status: 'ready',
});

const occupiedCells = (positions: Record<NodeId, Cell>): Set<string> =>
  new Set(Object.values(positions).map(cellKey));

/**
 * Fold an expand answer into the model.
 *
 * Nodes are placed once: anchors without a position go in a column at the
 * origin, a new neighbour goes on its parent's left when it calls the parent
 * and on the right when the parent calls it.
 */
export function mergeExpand(model: GraphModel, request: ExpandRequest, response: ExpandResponse): GraphModel {
  const nodes = { ...model.nodes };
  const edges = { ...model.edges };
  for (const node of response.nodes) nodes[node.id] = { ...nodes[node.id], ...node };
  for (const edge of response.edges) edges[edge.id] = edge;

  const positions = { ...model.positions };
  const occupied = occupiedCells(positions);

  const unplacedAnchors = request.nodeIds.filter((id) => nodes[id] && !positions[id]);
  Object.assign(positions, placeNodes(occupied, { c: -1, r: 0 }, 1, unplacedAnchors));

  // Group the unplaced nodes by (parent, side), in edge-id order so the result does not depend on the server's order.
  const groups = new Map<string, { parent: NodeId; side: Side; ids: Set<NodeId> }>();
  for (const edge of [...response.edges].sort((a, b) => a.id.localeCompare(b.id))) {
    for (const [fresh, parent, side] of [
      [edge.from, edge.to, -1],
      [edge.to, edge.from, 1],
    ] as [NodeId, NodeId, Side][]) {
      if (positions[fresh] || !positions[parent] || !nodes[fresh]) continue;
      const key = `${parent}|${side}`;
      const group = groups.get(key) ?? { parent, side, ids: new Set<NodeId>() };
      group.ids.add(fresh);
      groups.set(key, group);
    }
  }
  const taken = new Set<NodeId>();
  for (const { parent, side, ids } of groups.values()) {
    const mine = [...ids].filter((id) => !taken.has(id));
    mine.forEach((id) => taken.add(id));
    // The column height follows how many already sit on this side of the parent plus the new ones.
    const already = Object.values(edges).filter((e) => (side === -1 ? e.to === parent : e.from === parent) && positions[side === -1 ? e.from : e.to]).length;
    const placed = placeNodes(occupied, positions[parent], side, mine, already + mine.length);
    Object.assign(positions, placed);
  }
  // Anything still unplaced (no edge to a placed node) goes right of the origin.
  const stray = Object.keys(nodes).filter((id) => !positions[id]);
  Object.assign(positions, placeNodes(occupied, { c: 0, r: 0 }, 1, stray));

  const totals = { ...model.totals };
  const expanded = { ...model.expanded };
  for (const id of request.nodeIds) if (nodes[id]) expanded[id] = true;
  const more = { ...model.more };
  const truncated = response.page.truncated;
  for (const anchor of response.anchors) {
    if (truncated) delete totals[anchor.id];
    else totals[anchor.id] = anchor.total;
  }
  for (const id of request.nodeIds) {
    if (truncated && response.page.nextCursor) more[id] = { group: request.nodeIds, cursor: response.page.nextCursor };
    else delete more[id];
  }

  return {
    view: model.view,
    nodes,
    edges,
    positions,
    totals,
    expanded,
    more,
    incomplete: model.incomplete || response.page.incomplete,
    status: response.graphStatus,
  };
}

/**
 * Drop nodes that no edge touches, except `keep`: a node is in the view because it is linked to something
 * shown, so one left alone by a removal is a trace of what was removed.
 */
export function pruneOrphans(model: GraphModel, keep: readonly NodeId[] = []): GraphModel {
  const linked = new Set<NodeId>(keep);
  for (const e of Object.values(model.edges)) {
    linked.add(e.from);
    linked.add(e.to);
  }
  const orphans = Object.keys(model.nodes).filter((id) => !linked.has(id));
  return removeNodes(model, orphans);
}

/** The model without `ids`, their edges, and anything that pointed at them. */
export function removeNodes(model: GraphModel, ids: readonly NodeId[]): GraphModel {
  if (ids.length === 0) return model;
  const gone = new Set(ids);
  const keep = <T>(record: Record<string, T>, drop: (key: string, value: T) => boolean): Record<string, T> =>
    Object.fromEntries(Object.entries(record).filter(([key, value]) => !drop(key, value)));
  return {
    ...model,
    nodes: keep(model.nodes, (id) => gone.has(id)),
    edges: keep(model.edges, (_, e) => gone.has(e.from) || gone.has(e.to)),
    positions: keep(model.positions, (id) => gone.has(id)),
    totals: keep(model.totals, (id) => gone.has(id)),
    expanded: keep(model.expanded, (id) => gone.has(id)),
    more: Object.fromEntries(
      Object.entries(model.more)
        .filter(([id]) => !gone.has(id))
        .map(([id, m]) => [id, { ...m, group: m.group.filter((g) => !gone.has(g)) }]),
    ),
  };
}

/**
 * Drop the ids a revalidation was asked about and did not get back. Nodes added
 * while the call was in flight were not asked about, so they stay. Returns the ids
 * that left, for the selection and the trail.
 */
export function applyVisible(
  model: GraphModel,
  asked: readonly NodeId[],
  visible: readonly NodeId[],
  /** Nodes that stay though nothing links them (the focus). */
  keep: readonly NodeId[] = [],
): { model: GraphModel; /** taken away by the check */ revoked: NodeId[]; /** revoked, and left alone by it (still readable) */ pruned: NodeId[] } {
  const stillVisible = new Set(visible);
  const gone = asked.filter((id) => model.nodes[id] && !stillVisible.has(id));
  if (gone.length === 0) return { model, revoked: [], pruned: [] };
  const after = pruneOrphans(removeNodes(model, gone), keep);
  return { model: after, revoked: gone, pruned: Object.keys(model.nodes).filter((id) => !after.nodes[id] && !gone.includes(id)) };
}

export const nodeCount = (model: GraphModel): number => Object.keys(model.nodes).length;
