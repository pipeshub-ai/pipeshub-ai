import { createHash } from 'node:crypto';
import {
  canonicalDigest,
  canonicalJsonBytes,
} from '../contracts/canonical-json';
import { AuditEntry } from './audit';
import { ErasureJob } from './session-erasure';

/**
 * The erasure of conversations deleted before erasure existed (E7B): never
 * automatic, only an operator's explicit, one-time run of a plan they reviewed
 * (`enterprise_search/ops/erasure-backfill-cli.ts`). A dry run observes the candidates and
 * digests them; executing needs that digest, re-checks every batch against the
 * data before it enqueues it, and the erasure worker re-checks each
 * conversation before every step.
 */
export const BACKFILL_PLAN_VERSION = 1;
/** Conversations one batch enqueues (and the erasure worker then erases) before the next is looked at. */
export const BACKFILL_BATCH_SIZE = 100;
/** Conversations one read of the source takes. */
export const BACKFILL_SCAN_PAGE = 500;
export const BACKFILL_LEASE_NAME = 'agent-harness-erasure-backfill';
export const BACKFILL_LEASE_MS = 5 * 60_000;
export const BACKFILL_POLL_MS = 5_000;
/** How long one execute waits for the erasure worker before it reports `in_progress`. */
export const BACKFILL_DEFAULT_WAIT_MS = 30 * 60_000;
/** The audit chain's stream name for backfill records: they belong to no session journal. */
export const BACKFILL_AUDIT_STREAM = 'ops.erasure-backfill';
export const BACKFILL_BATCH_AUDIT_KIND = 'erasure.backfill_batch';
export const BACKFILL_COMPLETED_AUDIT_KIND = 'erasure.backfill_completed';

/** Where a planned conversation's data lives; the dry run counts each (artifacts live in the graph and are counted as they are erased). */
export const BACKFILL_STORES = [
  'chatSessions',
  'chatSessionMessages',
  'citations',
  'legacyCopies',
  'agentRuns',
] as const;
export type BackfillStore = (typeof BACKFILL_STORES)[number];
export type BackfillCounts = Record<BackfillStore, number>;

export const zeroBackfillCounts = (): BackfillCounts => ({
  chatSessions: 0,
  chatSessionMessages: 0,
  citations: 0,
  legacyCopies: 0,
  agentRuns: 0,
});

export const addBackfillCounts = (
  into: BackfillCounts,
  more: BackfillCounts,
): BackfillCounts => {
  const sum = { ...into };
  for (const store of BACKFILL_STORES) sum[store] += more[store];
  return sum;
};

/** One conversation as the conversation module sees it now. */
export interface ConversationObservation {
  orgId: string;
  sessionId: string;
  deleted: boolean;
  /** A delete of this release asked for its erasure (`erasureRequestedAt`). */
  erasureRequested: boolean;
  /** Principal ref of who deleted it, when recorded. */
  by: string | null;
  /** The session document's last write (epoch ms). */
  updatedAt: number;
  counts: BackfillCounts;
}

/**
 * The one predicate the dry run plans with and every batch re-checks with:
 * deleted, by a delete from before erasure existed, and with no erasure job.
 */
export const isBackfillCandidate = (
  observation: ConversationObservation,
  job: Pick<ErasureJob, 'status'> | null,
): boolean =>
  observation.deleted && !observation.erasureRequested && job === null;

/**
 * What the plan pins of a conversation: a later change to any of it makes the
 * plan stale. A stored item carries the same fields, so the tamper check
 * recomputes it.
 */
export const backfillFingerprint = (
  pinned: Pick<
    ConversationObservation,
    'orgId' | 'sessionId' | 'by' | 'updatedAt' | 'counts'
  >,
): string =>
  canonicalDigest({
    orgId: pinned.orgId,
    sessionId: pinned.sessionId,
    by: pinned.by,
    updatedAt: pinned.updatedAt,
    counts: pinned.counts,
  });

export type BackfillItemState =
  | 'planned'
  | 'enqueued'
  | 'erased'
  | 'withdrawn'
  | 'skipped_absent'
  | 'skipped_queued';

export interface BackfillItem {
  digest: string;
  orgId: string;
  sessionId: string;
  /** The org-local batch it is enqueued in. */
  batch: number;
  fingerprint: string;
  by: string | null;
  updatedAt: number;
  counts: BackfillCounts;
  state: BackfillItemState;
}

export interface BackfillOrgSummary {
  orgId: string;
  sessions: number;
  batches: number;
  counts: BackfillCounts;
}

/**
 * Where execution stands: the org (index into the plan's orgs) and its batch,
 * and how far that batch got. `next`: nothing done for it; `intended`: its
 * intent is recorded and nothing enqueued; `enqueued`: its jobs and audit
 * record committed, awaiting the worker.
 */
export interface BackfillCursor {
  org: number;
  batch: number;
  phase: 'next' | 'intended' | 'enqueued';
}

export type BackfillPlanState = 'persisting' | 'ready' | 'stale' | 'completed';

export interface BackfillPlan {
  digest: string;
  orgFilter: string | null;
  /** Who started executing it. */
  operator: string;
  createdAt: number;
  state: BackfillPlanState;
  /** Sorted by orgId; batches run in this order. */
  orgs: BackfillOrgSummary[];
  items: number;
  cursor: BackfillCursor;
  staleReason: string | null;
}

/** An org whose pre-upgrade deletions are erased: the backfill is never run for it again. */
export interface BackfillMarker {
  orgId: string;
  digest: string;
  operator: string;
  completedAt: number;
  erased: number;
  withdrawn: number;
  skipped: number;
}

export const INITIAL_BACKFILL_CURSOR: BackfillCursor = Object.freeze({
  org: 0,
  batch: 0,
  phase: 'next',
});

/** Points where execution has made a durable record; crash tests stop at each. */
export const BACKFILL_CHECKPOINTS = [
  'after_plan_persisted',
  'before_intent',
  'after_intent',
  'in_enqueue_transaction',
  'after_enqueue',
  'after_settle',
  'after_marker',
] as const;
export type BackfillCheckpoint = (typeof BACKFILL_CHECKPOINTS)[number];

/**
 * The plan's digest: over the plan version, the org filter, and every
 * candidate's org, session and fingerprint in session-id order. Incremental,
 * so a plan of any size is digested while it is read.
 */
export class BackfillDigester {
  private readonly hash = createHash('sha256');

  constructor(orgFilter: string | null) {
    this.line({
      v: BACKFILL_PLAN_VERSION,
      kind: 'erasure-backfill',
      orgFilter,
    });
  }

  add(item: Pick<BackfillItem, 'orgId' | 'sessionId' | 'fingerprint'>): void {
    this.line({
      orgId: item.orgId,
      sessionId: item.sessionId,
      fingerprint: item.fingerprint,
    });
  }

  digest(): string {
    return `sha256:${this.hash.digest('hex')}`;
  }

  private line(value: unknown): void {
    this.hash.update(canonicalJsonBytes(value));
    this.hash.update('\n');
  }
}

/** The audit record of one enqueued batch: counts and digests only. */
export function backfillBatchAuditEntry(
  plan: Pick<BackfillPlan, 'digest' | 'operator'>,
  orgId: string,
  batch: number,
  enqueued: readonly BackfillItem[],
  skipped: number,
): AuditEntry {
  const counts = enqueued.reduce(
    (sum, item) => addBackfillCounts(sum, item.counts),
    zeroBackfillCounts(),
  );
  return operationEntry(
    BACKFILL_BATCH_AUDIT_KIND,
    orgId,
    {
      planDigest: plan.digest,
      operator: plan.operator,
      batch,
      sessions: enqueued.length,
      skipped,
      ...counts,
    },
    enqueued.map((item) => item.sessionId).sort(),
  );
}

/** The audit record of an org whose backfill is complete. */
export function backfillCompletedAuditEntry(
  marker: BackfillMarker,
): AuditEntry {
  return operationEntry(
    BACKFILL_COMPLETED_AUDIT_KIND,
    marker.orgId,
    {
      planDigest: marker.digest,
      operator: marker.operator,
      erased: marker.erased,
      withdrawn: marker.withdrawn,
      skipped: marker.skipped,
    },
    [],
  );
}

/**
 * An operator action on the org's audit chain. It has no journal event (the
 * conversations it covers never ran on the harness), so `journalSeq` is 0,
 * which no journal event has; the session ids are bound by digest only.
 */
function operationEntry(
  kind: string,
  orgId: string,
  fields: Record<string, string | number>,
  sessionIds: readonly string[],
): AuditEntry {
  return {
    kind,
    sessionId: BACKFILL_AUDIT_STREAM,
    streamId: BACKFILL_AUDIT_STREAM,
    runId: null,
    journalSeq: 0,
    writer: 'node',
    attemptId: null,
    fence: null,
    fields,
    dataDigest: canonicalDigest({ orgId, sessionIds }),
  };
}

/** A typed refusal: the CLI prints the reason code and exits 1. */
export type BackfillRefusalCode =
  | 'plan_digest_mismatch'
  | 'plan_stale'
  | 'plan_tampered'
  | 'plan_unknown'
  | 'backfill_in_progress'
  | 'transactions_unavailable';

export class BackfillRefused extends Error {
  constructor(
    readonly reasonCode: BackfillRefusalCode,
    message: string,
  ) {
    super(message);
    this.name = 'BackfillRefused';
  }
}

const OPERATOR = /^[A-Za-z0-9][A-Za-z0-9_.@:+-]{0,127}$/;
export const isValidOperator = (operator: string): boolean =>
  OPERATOR.test(operator);
