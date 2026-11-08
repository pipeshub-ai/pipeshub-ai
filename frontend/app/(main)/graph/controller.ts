import type { ProcessedError } from '@/lib/api';
import { GraphApi } from './api';
import { classifyGraphFailure, type GraphFailure } from './errors';
import { applyVisible, emptyModel, mergeExpand, pruneOrphans, removeNodes, type GraphModel } from './model/graph-model';
import { useGraphStore } from './store';
import {
  MAX_EXPAND_ANCHORS,
  type ExpandRequest,
  type ExpandResponse,
  type NodeId,
  type ResolveRequest,
  type ResolveResponse,
  type RevalidateRequest,
  type RevalidateResponse,
} from './types';

export interface ExplorerApi {
  expand: (body: ExpandRequest, signal?: AbortSignal) => Promise<ExpandResponse>;
  revalidate: (body: RevalidateRequest, signal?: AbortSignal) => Promise<RevalidateResponse>;
  resolve: (body: ResolveRequest, signal?: AbortSignal) => Promise<ResolveResponse>;
}

export interface ExplorerSnapshot {
  model: GraphModel;
  failure: GraphFailure | null;
  /** Nodes the last Expand or Show more added: 0 means "nothing new", null that it was not one. */
  lastAdded: number | null;
  /** The selected node was taken away by an access re-check; cleared by the next selection or view. */
  selectionGone: boolean;
  loading: boolean;
  /** A move along the trail (the toolbar's arrows, the browser's Back or Forward) is under way: the address waits for where it lands. */
  moving: boolean;
}

const PAGE_LIMIT = 50;

/** The most nodes one view holds, drawn or not. Past it the person opens a node to start a new view. */
export const MAX_MODEL_NODES = 5000;

const nodeTotal = (model: GraphModel): number => Object.keys(model.nodes).length;

/**
 * Everything the explorer does between the screen and the API: open a node, expand it,
 * continue a truncated read, re-open under new filters, move along the trail, and drop what
 * the server says is no longer visible. It holds the model; the store holds selection,
 * filters and the trail. Plain data in, plain data out, so it is tested without a renderer.
 *
 * A failed call never empties the view: a new view replaces the old one only when its first
 * answer is in, and the trail and the filters move only when that worked.
 */
export class ExplorerController {
  private snapshot: ExplorerSnapshot = { model: emptyModel(), failure: null, lastAdded: null, selectionGone: false, loading: false, moving: false };
  private listeners = new Set<() => void>();
  /** Bumped by every new view, so an answer to a question asked before it is ignored. */
  private generation = 0;
  private views = 0;
  private pending = 0;
  private moves = 0;
  private last: (() => Promise<unknown>) | null = null;
  /** Ids an access re-check removed in this view. An answer that was already on its way must not put them back. */
  private revoked = new Map<NodeId, number>();
  /** Counts re-checks that removed something: a request only has to drop what was revoked after it started. */
  private epoch = 0;
  private filterSeq = 0;
  /** Expands in flight, by anchors and cursor: a second click for the same page waits for the first. */
  private inFlight = new Set<string>();

  constructor(private readonly api: ExplorerApi = GraphApi) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ExplorerSnapshot => this.snapshot;

  /** Ids on screen, for revalidation. */
  ids = (): NodeId[] => Object.keys(this.snapshot.model.nodes);

  /**
   * Make `id` the centre of a new view. Returns whether it worked; when it did not, the old view,
   * the trail and the focus are untouched. With `track` the node joins the trail once it is on screen.
   */
  async open(id: NodeId, { track = true }: { track?: boolean } = {}): Promise<boolean> {
    this.last = () => this.open(id, { track });
    this.generation += 1;
    this.revoked.clear();
    this.inFlight.clear();
    const ok = await this.expandGroup([id], undefined, true);
    if (ok && track) useGraphStore.getState().focusOn(id);
    return ok;
  }

  /**
   * Start from an address (a link from a search hit, a record page, a repo and file). The server turns it
   * into a node id, or says it is not available (hidden and missing are one answer), or that the repo name
   * is ambiguous. Then it opens like any node.
   */
  async openAddress(request: ResolveRequest): Promise<boolean> {
    this.last = () => this.openAddress(request);
    this.generation += 1;
    const generation = this.generation;
    this.set({ failure: null });
    this.begin();
    let nodeId: NodeId;
    try {
      ({ nodeId } = await this.api.resolve(request));
    } catch (error) {
      if (generation === this.generation) this.set({ failure: classifyGraphFailure(error as ProcessedError) });
      return false;
    } finally {
      this.end();
    }
    if (generation !== this.generation) return false;
    return this.open(nodeId);
  }

  /** Re-open the focus under the current filters. */
  async reload(): Promise<boolean> {
    const { node } = useGraphStore.getState();
    return node ? this.open(node, { track: false }) : true;
  }

  /** Change a filter and re-open. If that fails the filter goes back, so the controls match what is drawn. */
  async applyFilters(patch: Parameters<ReturnType<typeof useGraphStore.getState>['setFilters']>[0]): Promise<boolean> {
    const store = useGraphStore.getState();
    const { direction, edgeTypes, includeTests } = store;
    const seq = ++this.filterSeq;
    store.setFilters(patch);
    const ok = await this.reload();
    // A later change has taken over: the controls follow that one, and this one has nothing to put back.
    if (seq !== this.filterSeq) return ok;
    if (!ok) useGraphStore.getState().setFilters({ direction, edgeTypes, includeTests });
    return ok;
  }

  /**
   * The browser's Back or Forward names a centre and the trail position its history entry was made at. When that
   * position still holds the node, the trail moves to it (however far along), as the toolbar's arrows do, and a
   * Retry repeats the same move; anything else is a new, tracked step.
   */
  async follow(id: NodeId, at?: number): Promise<boolean> {
    return this.whileMoving(async () => {
      const { trail } = useGraphStore.getState();
      // The place the entry names, or if the trail has shifted since (a skipped node, a revoke) the nearest place
      // that still holds the node; a node that is not on the trail at all is a new step.
      const places = trail.flatMap((entry, i) => (entry === id ? [i] : []));
      if (at === undefined || places.length === 0) return this.open(id);
      const place = places.reduce((best, i) => (Math.abs(i - at) < Math.abs(best - at) ? i : best));
      const run = this.open(id, { track: false });
      this.last = () => this.follow(id, at);
      const ok = await run;
      if (ok) this.landOn(id, place);
      return ok;
    });
  }

  /**
   * Make the trail say what the picture shows. A later move or a revoke may have shifted the trail while a load was
   * in flight, so the place counts only if it still holds the node; otherwise the node's own place, or a new step.
   */
  private landOn(id: NodeId, index: number): void {
    const store = useGraphStore.getState();
    const at = store.trail[index] === id ? index : store.trail.indexOf(id);
    if (at >= 0) store.jumpTo(at);
    else store.focusOn(id);
  }

  /** One step along the trail. A node that is no longer available is dropped from it and the next one is tried. */
  async go(step: -1 | 1): Promise<boolean> {
    this.last = () => this.go(step);
    return this.whileMoving(() => this.walkTrail(step));
  }

  private async walkTrail(step: -1 | 1): Promise<boolean> {
    for (let index = useGraphStore.getState().trailIndex + step; ; ) {
      const { trail } = useGraphStore.getState();
      if (index < 0 || index >= trail.length) return false;
      const target = trail[index];
      const run = this.open(target, { track: false });
      this.last = () => this.go(step); // open set it to itself: a Retry has to move along the trail too
      if (await run) {
        this.landOn(target, index);
        return true;
      }
      if (this.snapshot.failure !== 'not-available') return false;
      useGraphStore.getState().forget([target]);
      // Every copy of the node left the trail, so the next candidate is whatever now lies in that direction.
      index = useGraphStore.getState().trailIndex + step;
    }
  }

  /** One hop from `ids` (any number: sent in requests of at most 20 anchors). */
  async expand(ids: readonly NodeId[], continuation?: { cursor: string; group: NodeId[] }): Promise<void> {
    this.last = () => this.expand(ids, continuation);
    const groups = continuation ? [continuation.group] : chunk(ids, MAX_EXPAND_ANCHORS);
    await Promise.all(groups.map((group) => this.expandGroup(group, continuation?.cursor)));
  }

  /** Continue the truncated read that `id` belongs to. */
  async showMore(id: NodeId): Promise<void> {
    const more = this.snapshot.model.more[id];
    if (more) await this.expand([id], { cursor: more.cursor, group: more.group });
  }

  /** Do the last thing again (the Retry on a failure). */
  async retry(): Promise<void> {
    await this.last?.();
  }

  /**
   * The result of a revalidation: drop the ids that were asked about and not returned, and what only
   * they linked. If the focus went, go back to the nearest trail entry that is still there, or say the
   * view is gone.
   */
  revoke(asked: readonly NodeId[], visible: readonly NodeId[]): void {
    const store = useGraphStore.getState();
    const { model, revoked, pruned } = applyVisible(this.snapshot.model, asked, visible, store.node ? [store.node] : []);
    if (revoked.length === 0) return;
    this.epoch += 1;
    for (const id of revoked) this.revoked.set(id, this.epoch);
    const selectionRevoked = store.selection !== null && revoked.includes(store.selection);
    this.set({ model, lastAdded: null, selectionGone: selectionRevoked || this.snapshot.selectionGone });
    const focusLeft = store.node !== null && revoked.includes(store.node);
    // Only what the check took away is forgotten. A node that merely lost its only link to it is still readable:
    // it leaves the picture with the rest of the view, but stays on the trail.
    store.forget(revoked);
    if (store.selection !== null && pruned.includes(store.selection)) useGraphStore.getState().select(null);
    if (focusLeft) void this.restoreFocus();
  }

  /** The person picked something (or nothing): the "selection is gone" message has done its job. */
  acknowledgeSelection(): void {
    if (this.snapshot.selectionGone) this.set({ selectionGone: false });
  }

  /** The access check cannot run for too long: stop showing what could not be confirmed. */
  clear(): void {
    this.generation += 1;
    this.revoked.clear();
    this.inFlight.clear();
    this.set({ model: emptyModel(), lastAdded: null });
  }

  /** Open the focus again (after a clear, or when the one on screen went). */
  async restoreFocus(): Promise<void> {
    for (;;) {
      const { node } = useGraphStore.getState();
      if (!node) {
        this.generation += 1;
        this.set({ model: emptyModel(), failure: 'revoked' });
        return;
      }
      if (await this.open(node, { track: false })) return;
      if (this.snapshot.failure !== 'not-available') return;
      useGraphStore.getState().forget([node]);
    }
  }

  private async expandGroup(group: NodeId[], cursor?: string, fresh = false): Promise<boolean> {
    const { direction, edgeTypes, includeTests } = useGraphStore.getState();
    const generation = this.generation;
    const request: ExpandRequest = {
      source: 'code',
      nodeIds: group,
      direction,
      ...(edgeTypes.length ? { edgeTypes } : {}),
      includeTests,
      limit: PAGE_LIMIT,
      ...(cursor ? { cursor } : {}),
    };
    if (!fresh && nodeTotal(this.snapshot.model) >= MAX_MODEL_NODES) {
      this.set({ failure: 'full' });
      return false;
    }
    const startEpoch = this.epoch;
    const key = `${group.join(',')}|${cursor ?? ''}`;
    if (!fresh) {
      if (this.inFlight.has(key)) return true;
      this.inFlight.add(key);
    }
    this.begin();
    try {
      const response = await this.api.expand(request);
      if (generation !== this.generation) return false;
      const base = fresh ? emptyModel() : this.snapshot.model;
      const merged = this.withoutRevoked(mergeExpand(base, request, response), startEpoch);
      if (fresh) merged.view = ++this.views;
      this.set({ model: merged, failure: null, lastAdded: fresh ? null : nodeTotal(merged) - nodeTotal(base), selectionGone: false });
      return true;
    } catch (error) {
      if (generation !== this.generation) return false;
      const failure = classifyGraphFailure(error as ProcessedError, Boolean(cursor));
      if (failure === 'bad-cursor') {
        // The cursor is only a place in a walk: start the walk again, silently.
        this.end();
        const ok = await this.expandGroup(group);
        this.begin();
        return ok;
      }
      // A failed read is never an empty graph: what was on screen stays, and the view says so.
      this.set({ failure });
      return false;
    } finally {
      // A stale answer must not release the guard of a newer request for the same page.
      if (generation === this.generation) this.inFlight.delete(key);
      this.end();
    }
  }

  /**
   * `model` without the nodes a re-check removed after the request started, and what only they linked. A request
   * that began after the removal is the server's word on who may see what, and is not second-guessed.
   */
  private withoutRevoked(model: GraphModel, startEpoch: number): GraphModel {
    const gone = [...this.revoked].filter(([id, at]) => at > startEpoch && model.nodes[id]).map(([id]) => id);
    if (gone.length === 0) return model;
    const { node } = useGraphStore.getState();
    return pruneOrphans(removeNodes(model, gone), node ? [node] : []);
  }

  /** Runs a move along the trail with `moving` raised, so a half-done move (a trail compacted before it lands) is never written to the address. */
  private async whileMoving<T>(run: () => Promise<T>): Promise<T> {
    this.moves += 1;
    this.set({ moving: true });
    try {
      return await run();
    } finally {
      this.moves -= 1;
      if (this.moves === 0) this.set({ moving: false });
    }
  }

  private begin(): void {
    this.pending += 1;
    this.set({ loading: true });
  }

  private end(): void {
    this.pending = Math.max(0, this.pending - 1);
    if (this.pending === 0) this.set({ loading: false });
  }

  private set(patch: Partial<ExplorerSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }
}

function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}
