import type { ChatKnowledgeFilters } from '@/chat/types';

/** The filter key a node is sent under. */
export type SelectionBucket = 'apps' | 'recordGroups' | 'records';

/**
 * - `checked`: the node itself is selected.
 * - `inherited`: an ancestor is selected, so the node is included and locked.
 * - `indeterminate`: something under the node is selected.
 */
export type CheckState = 'checked' | 'inherited' | 'indeterminate' | 'unchecked';

export interface SelectableNode {
  id: string;
  nodeType: string;
  /** Ids of every node above this one in the tree, root first. */
  ancestorIds: string[];
  /**
   * Set on a collection where collections are kept apart from apps (a saved
   * agent's or a project's sources); elsewhere a collection goes under `apps`.
   */
  bucket?: 'kb';
}

/** Ancestors of a selected node, as recorded when it was picked. */
export type AncestorLookup = (id: string) => readonly string[] | undefined;

/** Every key a selection is held under. */
export const SELECTION_KEYS = ['apps', 'kb', 'recordGroups', 'records', 'recordsExact'] as const;
/** The keys that select below app level. */
export const BELOW_APP_KEYS = ['recordGroups', 'records', 'recordsExact'] as const;
const BUCKETS = SELECTION_KEYS;
/** The keys whose nodes are selected with everything under them. */
const SUBTREE_KEYS = ['apps', 'kb', 'recordGroups', 'records'] as const;

/** The API refuses a request that selects more nodes than this. */
export const MAX_SELECTED_NODES = 200;

export function bucketFor(nodeType: string): SelectionBucket {
  if (nodeType === 'app' || nodeType === 'kb') return 'apps';
  if (nodeType === 'recordGroup') return 'recordGroups';
  return 'records';
}

export function selectedIds(filters: ChatKnowledgeFilters): string[] {
  return BUCKETS.flatMap((bucket) => filters[bucket] ?? []);
}

/** True for chat filters, and for the stored `appliedFilters` of a message, with anything selected. */
export function hasAnyFilter(
  filters: Partial<Record<(typeof BUCKETS)[number], readonly unknown[]>> | null | undefined
): boolean {
  return BUCKETS.some((bucket) => (filters?.[bucket]?.length ?? 0) > 0);
}

export function checkStateOf(
  filters: ChatKnowledgeFilters,
  node: SelectableNode,
  ancestorsOf: AncestorLookup
): CheckState {
  const selected = new Set(selectedIds(filters));
  if (selected.has(node.id)) return 'checked';
  // A record selected alone does not take what is under it.
  const withSubtree = new Set(SUBTREE_KEYS.flatMap((key) => filters[key] ?? []));
  if (node.ancestorIds.some((id) => withSubtree.has(id))) return 'inherited';
  for (const id of selected) {
    if (ancestorsOf(id)?.includes(node.id)) return 'indeterminate';
  }
  return 'unchecked';
}

/**
 * The filters after a click on a node's checkbox. Selecting a node takes its
 * whole subtree, so anything selected under it is dropped; a node included
 * through an ancestor is locked and the click changes nothing.
 */
export function toggleNode(
  filters: ChatKnowledgeFilters,
  node: SelectableNode,
  ancestorsOf: AncestorLookup
): ChatKnowledgeFilters {
  const state = checkStateOf(filters, node, ancestorsOf);
  if (state === 'inherited') return filters;

  const drop = (keep: (id: string) => boolean): ChatKnowledgeFilters => ({
    apps: (filters.apps ?? []).filter(keep),
    kb: (filters.kb ?? []).filter(keep),
    recordGroups: (filters.recordGroups ?? []).filter(keep),
    records: (filters.records ?? []).filter(keep),
    ...(filters.recordsExact ? { recordsExact: filters.recordsExact.filter(keep) } : {}),
  });

  if (state === 'checked') return drop((id) => id !== node.id);

  const next = drop((id) => !ancestorsOf(id)?.includes(node.id));
  const bucket = node.bucket ?? bucketFor(node.nodeType);
  return { ...next, [bucket]: [...(next[bucket] ?? []), node.id] };
}

/** The filters without one selected node, whichever key it is under. */
export function removeSelected(filters: ChatKnowledgeFilters, id: string): ChatKnowledgeFilters {
  const next: ChatKnowledgeFilters = { ...filters };
  for (const bucket of BUCKETS) {
    const ids = filters[bucket];
    if (ids) next[bucket] = ids.filter((selectedId) => selectedId !== id);
  }
  return next;
}

/** Whether two selections hold the same nodes under the same keys, in any order. */
export function sameSelection(a: ChatKnowledgeFilters, b: ChatKnowledgeFilters): boolean {
  return BUCKETS.every((bucket) => {
    const left = new Set(a[bucket] ?? []);
    const right = new Set(b[bucket] ?? []);
    return left.size === right.size && [...left].every((id) => right.has(id));
  });
}

/** Which icon a selected node gets as a pill or chip. */
export type SelectedNodeKind = 'connector' | 'collection' | 'folder' | 'file';

export function selectedNodeKind(
  bucket: (typeof SELECTION_KEYS)[number],
  nodeType: string | undefined
): SelectedNodeKind {
  if (bucket === 'kb') return 'collection';
  if (bucket === 'apps') return nodeType === 'app' ? 'connector' : 'collection';
  if (bucket === 'recordsExact') return 'file';
  return nodeType === 'record' ? 'file' : 'folder';
}
