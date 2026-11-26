/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import {
  BackfillCursor,
  BackfillItem,
  BackfillItemState,
  BackfillMarker,
  BackfillPlan,
} from '../domain/erasure-backfill';
import { ErasureBackfillStore } from '../ports/erasure-backfill';

const sameCursor = (a: BackfillCursor, b: BackfillCursor): boolean =>
  a.org === b.org && a.batch === b.batch && a.phase === b.phase;

/** Backfill plans, items and markers in memory, on an injected clock. */
export class InMemoryErasureBackfillStore implements ErasureBackfillStore<unknown> {
  private readonly plans = new Map<string, BackfillPlan>();
  private readonly planItems = new Map<string, Map<string, BackfillItem>>();
  private readonly orgMarkers = new Map<string, BackfillMarker>();

  constructor(private readonly clock: () => number = Date.now) {}

  async now(): Promise<number> {
    return this.clock();
  }

  async plan(digest: string): Promise<BackfillPlan | null> {
    const plan = this.plans.get(digest);
    return plan === undefined ? null : structuredClone(plan);
  }

  async createPlan(plan: BackfillPlan): Promise<BackfillPlan> {
    if (!this.plans.has(plan.digest)) {
      this.plans.set(plan.digest, structuredClone(plan));
    }
    return structuredClone(this.plans.get(plan.digest) as BackfillPlan);
  }

  async putItems(items: readonly BackfillItem[]): Promise<void> {
    for (const item of items) {
      const byId =
        this.planItems.get(item.digest) ?? new Map<string, BackfillItem>();
      if (!byId.has(item.sessionId)) byId.set(item.sessionId, { ...item });
      this.planItems.set(item.digest, byId);
    }
  }

  /** Test hook: a stored item changed behind the store's back. */
  tamper(
    digest: string,
    sessionId: string,
    change: Partial<BackfillItem>,
  ): void {
    const item = this.planItems.get(digest)?.get(sessionId);
    if (item === undefined) throw new Error(`no item ${sessionId}`);
    Object.assign(item, change);
  }

  async setPlanState(
    digest: string,
    from: BackfillPlan['state'],
    to: BackfillPlan['state'],
    detail: { staleReason?: string } = {},
  ): Promise<boolean> {
    const plan = this.plans.get(digest);
    if (plan?.state !== from) return false;
    plan.state = to;
    if (detail.staleReason !== undefined) plan.staleReason = detail.staleReason;
    return true;
  }

  async items(
    digest: string,
    afterSessionId: string | null,
    limit: number,
  ): Promise<BackfillItem[]> {
    return this.sorted(digest)
      .filter(
        (item) => afterSessionId === null || item.sessionId > afterSessionId,
      )
      .slice(0, limit);
  }

  async batchItems(
    digest: string,
    orgId: string,
    batch: number,
  ): Promise<BackfillItem[]> {
    return this.sorted(digest).filter(
      (item) => item.orgId === orgId && item.batch === batch,
    );
  }

  async moveCursor(
    digest: string,
    from: BackfillCursor,
    to: BackfillCursor,
  ): Promise<boolean> {
    const plan = this.plans.get(digest);
    if (plan === undefined || !sameCursor(plan.cursor, from)) return false;
    plan.cursor = { ...to };
    return true;
  }

  async setItemStates(
    digest: string,
    states: ReadonlyArray<{ sessionId: string; state: BackfillItemState }>,
  ): Promise<void> {
    const byId = this.planItems.get(digest);
    for (const { sessionId, state } of states) {
      const item = byId?.get(sessionId);
      if (item !== undefined) item.state = state;
    }
  }

  async marker(orgId: string): Promise<BackfillMarker | null> {
    const marker = this.orgMarkers.get(orgId);
    return marker === undefined ? null : { ...marker };
  }

  async markers(): Promise<BackfillMarker[]> {
    return [...this.orgMarkers.values()]
      .sort((a, b) => (a.orgId < b.orgId ? -1 : 1))
      .map((marker) => ({ ...marker }));
  }

  async putMarker(marker: BackfillMarker): Promise<boolean> {
    if (this.orgMarkers.has(marker.orgId)) return false;
    this.orgMarkers.set(marker.orgId, { ...marker });
    return true;
  }

  private sorted(digest: string): BackfillItem[] {
    return [...(this.planItems.get(digest)?.values() ?? [])]
      .sort((a, b) => (a.sessionId < b.sessionId ? -1 : 1))
      .map((item) => ({ ...item, counts: { ...item.counts } }));
  }
}
