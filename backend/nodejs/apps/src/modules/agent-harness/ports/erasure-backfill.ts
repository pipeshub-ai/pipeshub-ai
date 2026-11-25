import {
  BackfillCursor,
  BackfillItem,
  BackfillItemState,
  BackfillMarker,
  BackfillPlan,
  ConversationObservation,
} from '../domain/erasure-backfill';

/** The conversation module's side of the backfill: deleted conversations, observed now. */
export interface BackfillSource {
  /**
   * Deleted conversations with a session id after `afterSessionId`, in
   * session-id order, at most `limit`; only `orgId`'s when given. Marked and
   * unmarked alike: the backfill's predicate is applied by its caller.
   */
  deletedAfter(
    afterSessionId: string | null,
    limit: number,
    orgId: string | null,
  ): Promise<ConversationObservation[]>;
  /** Exactly these sessions of the org as they are now, deleted or not; an absent one is left out. */
  observe(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<ConversationObservation[]>;
}

/**
 * Plans being executed, their items, and the per-org completion markers. A
 * plan's cursor moves only by compare-and-set, so a second executor that lost
 * the lease cannot move it, and a batch is enqueued and audited once.
 */
export interface ErasureBackfillStore<Tx> {
  now(): Promise<number>;
  plan(digest: string): Promise<BackfillPlan | null>;
  /** Stores the plan if no plan has its digest; answers the stored one either way. */
  createPlan(plan: BackfillPlan): Promise<BackfillPlan>;
  /** Idempotent by (digest, sessionId): a second put of the same item changes nothing. */
  putItems(items: readonly BackfillItem[]): Promise<void>;
  /** `persisting` → `ready`, `ready` → `stale` or `completed`; false when the plan was not in `from`. */
  setPlanState(
    digest: string,
    from: BackfillPlan['state'],
    to: BackfillPlan['state'],
    detail?: { staleReason?: string },
  ): Promise<boolean>;
  /** The plan's items after `afterSessionId`, in session-id order. */
  items(
    digest: string,
    afterSessionId: string | null,
    limit: number,
  ): Promise<BackfillItem[]>;
  /** One org's batch, in session-id order. */
  batchItems(
    digest: string,
    orgId: string,
    batch: number,
  ): Promise<BackfillItem[]>;
  /** Moves the cursor from exactly `from` to `to`; false when it is no longer at `from`. */
  moveCursor(
    digest: string,
    from: BackfillCursor,
    to: BackfillCursor,
    tx?: Tx,
  ): Promise<boolean>;
  setItemStates(
    digest: string,
    states: ReadonlyArray<{ sessionId: string; state: BackfillItemState }>,
    tx?: Tx,
  ): Promise<void>;
  marker(orgId: string): Promise<BackfillMarker | null>;
  markers(): Promise<BackfillMarker[]>;
  /** Inserts the org's marker; false (nothing written) when the org already has one. */
  putMarker(marker: BackfillMarker, tx?: Tx): Promise<boolean>;
}
