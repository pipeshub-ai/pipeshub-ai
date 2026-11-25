import { OrgSessionRetention } from '../domain/session-retention';

/** The orgs' retention periods, bound at `AGENT_HARNESS_SESSION_RETENTION`; unbound, `NO_RETENTION`. */
export interface SessionRetentionSettings {
  /** Every org that set a period. */
  all(): Promise<OrgSessionRetention[]>;
}

/** No org sets a period: conversations are kept until their owner deletes them. */
export const NO_RETENTION: SessionRetentionSettings = {
  all: () => Promise.resolve([]),
};

/**
 * Expires one conversation idle since before `before` (epoch ms) and enqueues its erasure, in one transaction;
 * false when a run of it is still going, or it moved on meanwhile. `detail` is logged with the expiry.
 */
export type ConversationExpirer = (
  orgId: string,
  sessionId: string,
  before: number,
  why: { days: number; detail?: Readonly<Record<string, string>> },
) => Promise<boolean>;

/**
 * A retention pass the retention sweeper runs after the orgs' own periods: it picks conversations by rules of its
 * own and expires each through `expire`, answering how many it expired. An extension point, bound to
 * `AGENT_HARNESS_RETENTION_PASS`; unbound, no such pass runs.
 */
export interface RetentionPass {
  run(
    now: number,
    periods: readonly OrgSessionRetention[],
    expire: ConversationExpirer,
  ): Promise<number>;
}

export interface ErasureRequestCursor {
  requestedAt: number;
  sessionId: string;
}

export interface AwaitingErasure extends ErasureRequestCursor {
  orgId: string;
  /** Principal ref of who deleted it; null when retention expired it. */
  by: string | null;
}

/** The conversation module's side of retention: which sessions are idle, and expiring one. */
export interface ConversationRetention<Tx> {
  /** Ids of the org's live conversations last active before `before` (epoch ms), oldest first. */
  idleSince(orgId: string, before: number, limit: number): Promise<string[]>;
  /** Marks it deleted if it is still live and idle; false when it moved on meanwhile. */
  expire(
    orgId: string,
    sessionId: string,
    before: number,
    tx: Tx,
  ): Promise<boolean>;
  /**
   * Deleted conversations whose delete asked for erasure (`erasureRequestedAt`)
   * and that are not erased yet, after `after` in request order: the sweeper
   * enqueues them again in case the delete's own enqueue was lost. A
   * conversation deleted before erasure existed never qualifies (E7B).
   */
  awaitingErasure(
    after: ErasureRequestCursor | null,
    limit: number,
  ): Promise<AwaitingErasure[]>;
}

/** The T5 stores the retention sweeper prunes. */
export interface RetentionStore {
  now(): Promise<number>;
  /** Unreferenced payloads stored before `before` whose writer run ended or never existed. */
  collectPayloads(before: number, limit: number): Promise<number>;
  /** Command receipts written before `before` of runs that ended. */
  expireCommandReceipts(before: number, limit: number): Promise<number>;
  expireAdmissionReceipts(before: number, limit: number): Promise<number>;
  /** Orgs that ever used the harness (they hold a data key). */
  harnessOrgs(): Promise<string[]>;
}
