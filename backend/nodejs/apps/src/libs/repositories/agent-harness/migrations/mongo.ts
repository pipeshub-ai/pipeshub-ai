import type { Db, Document } from 'mongodb';
import {
  indexMigration,
  HarnessMigration,
  IndexDefinition,
  IndexMigrationResult,
} from '../../../utils/mongo-index-migration';
import {
  PLAIN_WHILE_EMPTY,
  PLAINTEXT_INPUT_FIELDS,
  SEALED_INPUT_FIELDS,
  type RunInputSeal,
  type StoredQueryInput,
} from '../../../../modules/agent-harness/application/run-input-seal';
import type { SealContext } from '../../../../modules/agent-harness/domain/data-protection';
import {
  AUDIT_PENDING,
  AUDIT_PENDING_ORDER_INDEX,
  linkAllPendingAudit,
  pendingAuditOrgs,
  AUDIT_RECORDS,
} from '../audit-linking/mongo';
import {
  AUDIT_RECORDS_BY_RUN_INDEX,
  AUDIT_RECORDS_OF_RUNS,
  AgentAuditAnchorModel,
  AgentAuditHeadModel,
  AgentAuditRecordModel,
} from '../audit/schema/audit.schema';
import {
  AgentBudgetReservationModel,
  BUDGET_RESERVATIONS_EXPIRY_INDEX,
  BUDGET_RESERVATIONS_HOLDS_INDEX,
} from '../budget-ledger/schema/budget-ledger.schema';
import { AgentHarnessDataKeyModel } from '../data-protection/schema/data-protection.schema';
import {
  RUN_END_NOTICE_CLAIM_TTL_SECONDS,
  RUN_END_NOTICE_CLAIMS_EXPIRY_INDEX,
  RUN_WATCH_LEASES_BY_PERSON_INDEX,
  RUN_WATCH_LEASES_EXPIRY_INDEX,
  RunEndNoticeClaimModel,
  RunWatchLeaseModel,
} from '../inbox/schema/inbox.schema';
import {
  AgentErasureBackfillItemModel,
  BACKFILL_ITEMS_BY_BATCH_INDEX,
  BACKFILL_ITEMS_BY_SESSION_INDEX,
} from '../erasure-backfill/schema/erasure-backfill.schema';
import {
  AgentRunModel,
  RUNS_BY_ACTOR_INDEX,
  RUNS_BY_AGENT_INDEX,
  RUNS_BY_ACTOR_CURSOR_INDEX,
  RUNS_BY_ACTOR_AGENT_INDEX,
  CHILD_RUNS_BY_STATUS_INDEX,
  RUNS_BY_PARENT_INDEX,
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentEventCounterModel,
  AgentEventModel,
  AgentWorkspaceHeadModel,
  ADMISSION_RECEIPTS_BY_SESSION_INDEX,
  RECEIPTS_BY_AGE_INDEX,
  RUNS_BY_SESSION_INDEX,
  AgentNextRunModel,
  NEXT_RUNS_BY_SESSION_INDEX,
  NEXT_RUNS_BY_STATUS_INDEX,
  CLAIM_BY_PRIORITY_INDEX,
  STRANDED_RUNS_INDEX,
} from '../execution/schema/execution.schema';
import {
  SessionEventModel,
  SessionStreamCounterModel,
  AgentJournalPayloadModel,
  PAYLOAD_GC_INDEX,
  PAYLOAD_OWNER_INDEX,
  SESSION_EVENTS_BY_SESSION_INDEX,
} from '../journal/schema/journal.schema';
import {
  AgentMemoryModel,
  SUPERSEDED_MEMORIES_INDEX,
  MEMORIES_BY_AGENT_INDEX,
  MEMORIES_BY_OWNER_INDEX,
  MEMORIES_BY_RUN_INDEX,
  MEMORIES_BY_SESSION_INDEX,
  PENDING_MEMORIES_INDEX,
} from '../memory/schema/memory.schema';
import {
  AgentSandboxSnapshotModel,
  SNAPSHOTS_BY_AGE_INDEX,
  SNAPSHOTS_BY_SESSION_INDEX,
} from '../sandbox-snapshots/schema/sandbox-snapshots.schema';
import {
  AgentScheduleModel,
  AgentScheduleFiringModel,
  FIRINGS_BY_SCHEDULE_INDEX,
  FIRINGS_BY_SLOT_INDEX,
  FIRINGS_PENDING_INDEX,
  FIRINGS_WATCHED_INDEX,
  SCHEDULES_BY_ACTOR_INDEX,
  SCHEDULES_BY_ORG_INDEX,
  SCHEDULES_BY_TRIGGER_INDEX,
  SCHEDULES_DUE_INDEX,
  AgentScheduleCounterModel,
  COUNTERS_EXPIRY_INDEX,
  FIRINGS_WATCH_ROUND_INDEX,
} from '../schedules/schema/schedules.schema';
import {
  AgentSessionErasureModel,
  ERASURES_BY_SESSION_INDEX,
  ERASURES_DUE_INDEX,
} from '../session-erasure/schema/session-erasure.schema';
import {
  AgentSessionForkModel,
  SESSION_FORKS_BY_FORK_INDEX,
  SESSION_FORKS_BY_SOURCE_INDEX,
} from '../session-forks/schema/session-forks.schema';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../workspace/schema/workspace.schema';

const indexes = indexMigration('audit-pending', [
  {
    collection: AUDIT_PENDING,
    name: AUDIT_PENDING_ORDER_INDEX,
    key: { orgId: 1, streamId: 1, journalSeq: 1 },
  },
]);

/** Links what is pending in every org; returns how many orgs had some. */
export async function linkEveryPendingAudit(db: Db): Promise<number> {
  const orgs = await pendingAuditOrgs(db);
  for (const orgId of orgs) await linkAllPendingAudit(db, orgId);
  return orgs.length;
}

/**
 * D2 for PERF-C: the order audit appends are linked in. Down links what is
 * pending before it drops the index: the release before this one reads the
 * chain alone, so a rollback must leave no entry outside it. The chain's own
 * documents are the ones that release wrote; nothing of them is migrated.
 */
export const AUDIT_PENDING_MIGRATION: HarnessMigration = {
  ...indexes,
  async down(db) {
    await linkEveryPendingAudit(db);
    return indexes.down(db);
  },
};

/** D2 for AUDITUI: the admin download-audit page reads one kind of an org's chain, newest first. */
export const AUDIT_QUERIES_MIGRATION: HarnessMigration = indexMigration(
  'audit-queries',
  [
    {
      collection: AUDIT_RECORDS,
      name: 'orgId_1_kind_1_seq_-1',
      key: { orgId: 1, kind: 1, seq: -1 },
    },
  ],
);

/**
 * D2 for the audit search (J-18): a person or agent filter reads their runs
 * newest first, then those runs' records on the org's chain.
 */
export const AUDIT_SEARCH_MIGRATION: HarnessMigration = indexMigration(
  'audit-search',
  [
    {
      collection: AUDIT_RECORDS,
      name: AUDIT_RECORDS_BY_RUN_INDEX,
      key: { orgId: 1, runId: 1, seq: -1 },
      partialFilterExpression: AUDIT_RECORDS_OF_RUNS,
    },
    {
      collection: AgentRunModel.collection.collectionName,
      name: RUNS_BY_ACTOR_INDEX,
      key: { orgId: 1, actorId: 1, createdAt: -1 },
    },
    {
      collection: AgentRunModel.collection.collectionName,
      name: RUNS_BY_AGENT_INDEX,
      key: { orgId: 1, agentKey: 1, createdAt: -1 },
    },
  ],
);

/**
 * D2 for P6.5: the open reservations of a run tree past their expiry, which
 * every ledger operation on the tree returns first. Accounts and reservations
 * are otherwise read by `_id` only.
 */
export const BUDGET_LEDGER_MIGRATION = indexMigration('budget-ledger', [
  {
    collection: AgentBudgetReservationModel.collection.collectionName,
    name: BUDGET_RESERVATIONS_EXPIRY_INDEX,
    key: { orgId: 1, rootRunId: 1, state: 1, expiresAt: 1 },
  },
]);

/** WP-66: an org's open holds, summed under its shared budgets at every reservation. */
export const BUDGET_HOLDS_MIGRATION = indexMigration('budget-holds', [
  {
    collection: AgentBudgetReservationModel.collection.collectionName,
    name: BUDGET_RESERVATIONS_HOLDS_INDEX,
    key: { orgId: 1, state: 1, expiresAt: 1 },
    partialFilterExpression: { hold: true },
  },
]);

const CHILD_RUNS_ONLY = { 'parent.runId': { $exists: true } };

/**
 * D2 for P6.1: a parent's open children (`openChildren`, the cancel cascade)
 * and children an ended parent left (`orphanedChildren`). Partial, so only
 * child runs are indexed, though the build still scans every run once.
 */
export const CHILD_RUNS_MIGRATION = indexMigration('child-runs', [
  {
    collection: AgentRunModel.collection.collectionName,
    name: RUNS_BY_PARENT_INDEX,
    key: { orgId: 1, 'parent.runId': 1, status: 1 },
    partialFilterExpression: CHILD_RUNS_ONLY,
  },
  {
    collection: AgentRunModel.collection.collectionName,
    name: CHILD_RUNS_BY_STATUS_INDEX,
    key: { status: 1, createdAt: 1 },
    partialFilterExpression: CHILD_RUNS_ONLY,
  },
]);

const runs = AgentRunModel.collection.collectionName;

const sessionEvents = SessionEventModel.collection.collectionName;

/**
 * D2 for P0–P1 (runs, v1 events, receipts, counters, workspace heads, the
 * journal, data keys, the audit chain): the indexes `Model.createIndexes()`
 * and `autoIndex` built before D2. The schemas leave them unnamed, so each
 * carries Mongo's default name and a deployment's existing index is found
 * rather than built again under a second name.
 */
const CORE_INDEXES: readonly IndexDefinition[] = [
  {
    collection: runs,
    name: 'orgId_1_runId_1',
    key: { orgId: 1, runId: 1 },
    unique: true,
  },
  {
    collection: runs,
    name: 'activeSessionKey_1',
    key: { activeSessionKey: 1 },
    unique: true,
    partialFilterExpression: { activeSessionKey: { $type: 'string' } },
  },
  {
    collection: runs,
    name: 'status_1_leaseExpiresAt_1',
    key: { status: 1, leaseExpiresAt: 1 },
  },
  {
    collection: runs,
    name: 'status_1_harnessMajor_1_notBefore_1',
    key: { status: 1, harnessMajor: 1, notBefore: 1 },
  },
  {
    collection: runs,
    name: 'status_1_absoluteDeadlineAt_1',
    key: { status: 1, absoluteDeadlineAt: 1 },
  },
  {
    collection: runs,
    name: 'orgId_1_status_1_actorId_1',
    key: { orgId: 1, status: 1, actorId: 1 },
  },
  {
    collection: AgentEventModel.collection.collectionName,
    name: 'orgId_1_sessionId_1_eventSeq_1',
    key: { orgId: 1, sessionId: 1, eventSeq: 1 },
    unique: true,
  },
  {
    collection: AgentEventModel.collection.collectionName,
    name: 'orgId_1_runId_1_commitSeq_1',
    key: { orgId: 1, runId: 1, commitSeq: 1 },
  },
  {
    collection: AgentCommandReceiptModel.collection.collectionName,
    name: 'orgId_1_runId_1_commandId_1',
    key: { orgId: 1, runId: 1, commandId: 1 },
    unique: true,
  },
  {
    collection: AgentEventCounterModel.collection.collectionName,
    name: 'orgId_1_sessionId_1',
    key: { orgId: 1, sessionId: 1 },
    unique: true,
  },
  {
    collection: AgentAdmissionReceiptModel.collection.collectionName,
    name: 'scopeKey_1',
    key: { scopeKey: 1 },
    unique: true,
  },
  {
    collection: AgentWorkspaceHeadModel.collection.collectionName,
    name: 'orgId_1_workspaceId_1',
    key: { orgId: 1, workspaceId: 1 },
    unique: true,
  },
  {
    collection: sessionEvents,
    name: 'orgId_1_streamId_1_seq_1',
    key: { orgId: 1, streamId: 1, seq: 1 },
    unique: true,
  },
  {
    collection: sessionEvents,
    name: 'orgId_1_runId_1_seq_1',
    key: { orgId: 1, runId: 1, seq: 1 },
  },
  {
    collection: SessionStreamCounterModel.collection.collectionName,
    name: 'orgId_1_streamId_1',
    key: { orgId: 1, streamId: 1 },
    unique: true,
  },
  {
    collection: AgentHarnessDataKeyModel.collection.collectionName,
    name: 'orgId_1_version_1',
    key: { orgId: 1, version: 1 },
    unique: true,
  },
  {
    collection: AgentAuditRecordModel.collection.collectionName,
    name: 'orgId_1_seq_1',
    key: { orgId: 1, seq: 1 },
    unique: true,
  },
  {
    collection: AgentAuditHeadModel.collection.collectionName,
    name: 'orgId_1',
    key: { orgId: 1 },
    unique: true,
  },
  {
    collection: AgentAuditHeadModel.collection.collectionName,
    name: 'anchoredSeq_1',
    key: { anchoredSeq: 1 },
  },
  {
    collection: AgentAuditAnchorModel.collection.collectionName,
    name: 'orgId_1_seq_1',
    key: { orgId: 1, seq: 1 },
  },
];

export const CORE_STORE_MIGRATION = indexMigration('core-store', CORE_INDEXES);

/** ADR-UI-01 D1 (UI-B1): a person's runs across their conversations, newest first, with an exact page cursor. */
export const RUN_LISTS_MIGRATION = indexMigration('run-lists', [
  {
    collection: AgentRunModel.collection.collectionName,
    name: RUNS_BY_ACTOR_CURSOR_INDEX,
    key: { orgId: 1, actorId: 1, createdAt: -1, runId: -1 },
  },
  {
    collection: AgentRunModel.collection.collectionName,
    name: RUNS_BY_ACTOR_AGENT_INDEX,
    key: { orgId: 1, actorId: 1, agentKey: 1, createdAt: -1, runId: -1 },
  },
]);

/**
 * G2 (ADR-INBOX-01): a person's watch leases are read by person and conversation, and a lease or a run-notice claim
 * nobody needs any more is removed by the server.
 */
export const INBOX_MIGRATION = indexMigration('inbox', [
  {
    collection: RunWatchLeaseModel.collection.collectionName,
    name: RUN_WATCH_LEASES_BY_PERSON_INDEX,
    key: { orgId: 1, userId: 1, sessionId: 1, until: 1 },
  },
  {
    collection: RunWatchLeaseModel.collection.collectionName,
    name: RUN_WATCH_LEASES_EXPIRY_INDEX,
    key: { expiresAt: 1 },
    expireAfterSeconds: 0,
  },
  {
    collection: RunEndNoticeClaimModel.collection.collectionName,
    name: RUN_END_NOTICE_CLAIMS_EXPIRY_INDEX,
    key: { createdAt: 1 },
    expireAfterSeconds: RUN_END_NOTICE_CLAIM_TTL_SECONDS,
  },
]);

/**
 * E7B: a plan's batches and its items in session order. The backfill CLI runs
 * this itself (with the erasure-store migration) before it touches anything,
 * so it works on an install with the harness off.
 */
export const ERASURE_BACKFILL_MIGRATION = indexMigration('erasure-backfill', [
  {
    collection: AgentErasureBackfillItemModel.collection.collectionName,
    name: BACKFILL_ITEMS_BY_BATCH_INDEX,
    key: { digest: 1, orgId: 1, batch: 1, sessionId: 1 },
  },
  {
    collection: AgentErasureBackfillItemModel.collection.collectionName,
    name: BACKFILL_ITEMS_BY_SESSION_INDEX,
    key: { digest: 1, sessionId: 1 },
  },
]);

/** D2 for E7 + T5: the erasure jobs, and the lookups erasure and GC make by session, owner and age. */
const ERASURE_INDEXES: readonly IndexDefinition[] = [
  {
    collection: AgentSessionErasureModel.collection.collectionName,
    name: ERASURES_DUE_INDEX,
    key: { status: 1, nextAttemptAt: 1 },
  },
  {
    collection: AgentSessionErasureModel.collection.collectionName,
    name: ERASURES_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1 },
    unique: true,
  },
  {
    collection: AgentRunModel.collection.collectionName,
    name: RUNS_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1 },
  },
  {
    collection: SessionEventModel.collection.collectionName,
    name: SESSION_EVENTS_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1, streamId: 1, seq: 1 },
  },
  {
    collection: AgentJournalPayloadModel.collection.collectionName,
    name: PAYLOAD_OWNER_INDEX,
    key: { orgId: 1, 'owner.sessionId': 1 },
    partialFilterExpression: { owner: { $exists: true } },
  },
  {
    collection: AgentJournalPayloadModel.collection.collectionName,
    name: PAYLOAD_GC_INDEX,
    key: { createdAt: 1 },
    partialFilterExpression: { referenced: false },
  },
  {
    collection: AgentCommandReceiptModel.collection.collectionName,
    name: RECEIPTS_BY_AGE_INDEX,
    key: { createdAt: 1 },
  },
  {
    collection: AgentAdmissionReceiptModel.collection.collectionName,
    name: RECEIPTS_BY_AGE_INDEX,
    key: { createdAt: 1 },
  },
  {
    collection: AgentAdmissionReceiptModel.collection.collectionName,
    name: ADMISSION_RECEIPTS_BY_SESSION_INDEX,
    key: { 'result.sessionId': 1 },
  },
];

export const ERASURE_STORE_MIGRATION = indexMigration(
  'erasure-store',
  ERASURE_INDEXES,
);

export type ErasureStoreMigration = IndexMigrationResult;

export const migrateErasureStore = (db: Db): Promise<ErasureStoreMigration> =>
  ERASURE_STORE_MIGRATION.up(db);

/** Keeps every document, so a rollback loses no job or payload. */
export const revertErasureStore = (db: Db): Promise<ErasureStoreMigration> =>
  ERASURE_STORE_MIGRATION.down(db);

/** Superseded memories by the correction that replaced them: they are deleted and erased with it (FIX-R2 C2). */
export const MEMORY_CORRECTIONS_MIGRATION = indexMigration(
  'memory-corrections',
  [
    {
      collection: AgentMemoryModel.collection.collectionName,
      name: SUPERSEDED_MEMORIES_INDEX,
      key: { orgId: 1, supersededBy: 1 },
      partialFilterExpression: { supersededBy: { $type: 'string' } },
    },
  ],
);

const memories = AgentMemoryModel.collection.collectionName;

/** D2 for P7.3: lists and recall by owner or agent, erasure by session, settlement by run. */
export const MEMORY_MIGRATION = indexMigration('memories', [
  {
    collection: memories,
    name: MEMORIES_BY_OWNER_INDEX,
    key: { orgId: 1, ownerId: 1, createdAt: -1 },
  },
  {
    collection: memories,
    name: MEMORIES_BY_AGENT_INDEX,
    key: { orgId: 1, agentKey: 1, createdAt: -1 },
  },
  {
    collection: memories,
    name: MEMORIES_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1 },
  },
  {
    collection: memories,
    name: MEMORIES_BY_RUN_INDEX,
    key: { orgId: 1, runId: 1 },
  },
]);

/** Pending proposals by age, only while pending: the run sweeper settles what a run's ending missed. */
export const MEMORY_SETTLEMENT_MIGRATION = indexMigration('memory-settlement', [
  {
    collection: AgentMemoryModel.collection.collectionName,
    name: PENDING_MEMORIES_INDEX,
    key: { createdAt: 1 },
    partialFilterExpression: { status: 'pending' },
  },
]);

const nextRuns = AgentNextRunModel.collection.collectionName;

/**
 * D2 for SS2: a session's next runs in line (enqueue position, drain, erasure)
 * and the sweeper's look for pending ones a drain missed.
 */
export const NEXT_RUNS_MIGRATION = indexMigration('next-runs', [
  {
    collection: nextRuns,
    name: NEXT_RUNS_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1, status: 1, createdAt: 1 },
  },
  {
    collection: nextRuns,
    name: NEXT_RUNS_BY_STATUS_INDEX,
    key: { status: 1, createdAt: 1 },
  },
]);

/**
 * D2 for G-45: claim-next reads the queued v2 runs highest class first, oldest
 * notBefore within a class.
 */
export const PRIORITY_CLAIMS_MIGRATION = indexMigration('priority-claims', [
  {
    collection: AgentRunModel.collection.collectionName,
    name: CLAIM_BY_PRIORITY_INDEX,
    key: { status: 1, harnessMajor: 1, priorityRank: 1, notBefore: 1 },
  },
]);

/**
 * OWD-12: the snapshot TTL sweep reads an org's oldest snapshots; erasure drops a session's. The id is the one
 * installs recorded when it shipped.
 */
export const SANDBOX_SNAPSHOTS_MIGRATION = indexMigration('sandbox-snapshots', [
  {
    collection: AgentSandboxSnapshotModel.collection.collectionName,
    name: SNAPSHOTS_BY_AGE_INDEX,
    key: { orgId: 1, takenAt: 1 },
  },
  {
    collection: AgentSandboxSnapshotModel.collection.collectionName,
    name: SNAPSHOTS_BY_SESSION_INDEX,
    key: { orgId: 1, sessionId: 1 },
  },
]);

const runCopy = (
  d: Document,
): {
  orgId: string;
  context: { runId: string };
  input: StoredQueryInput | null | undefined;
} => ({
  orgId: d['orgId'] as string,
  context: { runId: d['runId'] as string },
  input: d['input'] as StoredQueryInput | null | undefined,
});

/**
 * Where each harness collection keeps a run input: `at` prefixes its fields,
 * `copy` reads the document's org, what its seal is bound to and the input.
 */
const INPUT_COPIES: ReadonlyArray<{
  collection: string;
  at: string;
  copy: (d: Document) => {
    orgId: string;
    context: SealContext;
    input: StoredQueryInput | null | undefined;
  };
}> = [
  {
    collection: AgentRunModel.collection.collectionName,
    at: '',
    copy: runCopy,
  },
  {
    collection: AgentAdmissionReceiptModel.collection.collectionName,
    at: 'result.run.',
    copy: (d) => runCopy((d['result'] as Document)['run'] as Document),
  },
  {
    collection: AgentCommandReceiptModel.collection.collectionName,
    at: 'receipt.run.',
    copy: (d) => runCopy((d['receipt'] as Document)['run'] as Document),
  },
  {
    collection: AgentScheduleModel.collection.collectionName,
    at: '',
    copy: (d) => ({
      orgId: d['orgId'] as string,
      context: { scheduleId: d['_id'] as string },
      input: d['input'] as StoredQueryInput | null,
    }),
  },
];

/** The input fields stored in plaintext before LCFIX (the query), GAPS-1 (the details) and GAPS2-1 (an inline history). */
const PLAINTEXT = PLAINTEXT_INPUT_FIELDS;

/**
 * Matched by presence, not by value: admission writes a history once and
 * nothing rewrites it (a replica sealing it or an erasure removes it), and
 * matching by value would carry a history close to the document limit twice
 * in one update command, past MongoDB's BSON limit.
 */
const WRITTEN_ONCE: ReadonlySet<string> = new Set(['previousConversations']);

export type RunInputMigration = Record<string, number>;

/**
 * Seals the plaintext run inputs stored before LCFIX, GAPS-1 and GAPS2-1, at every boot
 * until none is left. Each document moves in one conditional update that sets
 * the sealed fields and unsets the plaintext ones only while every field it
 * seals still holds what was read (or, written once, is still there), so a
 * crash, a second replica or a concurrent write never loses one; readers take either form meanwhile. Runs
 * go first: a receipt copies its run.
 */
export async function sealStoredRunInputs(
  seal: RunInputSeal,
  db: Db,
): Promise<RunInputMigration> {
  const sealed: RunInputMigration = {};
  for (const { collection, at, copy } of INPUT_COPIES) {
    const rows = db.collection(collection);
    let count = 0;
    const cursor = rows.find({
      $or: [
        { [`${at}input.query`]: { $type: 'string' } },
        ...PLAINTEXT.slice(1).map((field) =>
          // An empty history stays as it is (a first turn's): a row with nothing else to seal is never read again.
          PLAIN_WHILE_EMPTY.has(field)
            ? { [`${at}input.${field}.0`]: { $exists: true } }
            : { [`${at}input.${field}`]: { $exists: true } },
        ),
      ],
    });
    for await (const document of cursor) {
      const { orgId, context, input } = copy(document);
      if (!input) continue;
      const moved = await seal.sealInput(orgId, context, input);
      if (moved === input) continue;
      // The fields sealed now: an empty history left as it is is neither matched nor unset.
      const read = PLAINTEXT.filter(
        (field) => input[field] !== undefined && moved[field] === undefined,
      );
      const done = await rows.updateOne(
        {
          _id: document._id,
          ...Object.fromEntries(
            read.map((field) => [
              `${at}input.${field}`,
              WRITTEN_ONCE.has(field) ? { $exists: true } : input[field],
            ]),
          ),
        },
        {
          $set: Object.fromEntries(
            SEALED_INPUT_FIELDS.filter(
              (field) => moved[field] !== input[field],
            ).map((field) => [`${at}input.${field}`, moved[field]]),
          ),
          $unset: Object.fromEntries(
            read.map((field) => [`${at}input.${field}`, '']),
          ),
        },
      );
      count += done.modifiedCount;
    }
    sealed[collection] = count;
  }
  return sealed;
}

/**
 * LC2FIX: a `/review` message saved before the fix holds the workspace's diff
 * in plaintext; it keeps only what was typed now (`storedQuestion`), the diff
 * staying in the run's sealed input. One idempotent update, at every boot.
 */
export async function typedReviewMessages(db: Db): Promise<number> {
  const done = await db.collection('chatSessionMessages').updateMany(
    {
      messageType: 'user_query',
      'command.owner': 'pipeshub',
      'command.name': 'review',
      'command.typed': { $type: 'string' },
      $expr: { $ne: ['$content', '$command.typed'] },
    },
    [{ $set: { content: '$command.typed' } }],
  );
  return done.modifiedCount;
}
const schedules = AgentScheduleModel.collection.collectionName;

const firings = AgentScheduleFiringModel.collection.collectionName;

/**
 * D2 for P8a: a schedule's lists and limits (by actor, by org), the due-slot
 * scan, event triggers by kind; a firing's unique slot (exactly once per
 * slot), its schedule's history, the pending-lease scan and the approval watch.
 */
export const SCHEDULES_MIGRATION = indexMigration('schedules', [
  {
    collection: schedules,
    name: SCHEDULES_BY_ACTOR_INDEX,
    key: { orgId: 1, actorId: 1, createdAt: -1 },
  },
  {
    collection: schedules,
    name: SCHEDULES_BY_ORG_INDEX,
    key: { orgId: 1, createdAt: -1 },
  },
  {
    collection: schedules,
    name: SCHEDULES_DUE_INDEX,
    key: { status: 1, nextFireAt: 1 },
  },
  {
    collection: schedules,
    name: SCHEDULES_BY_TRIGGER_INDEX,
    key: { 'trigger.kind': 1, status: 1, orgId: 1 },
  },
  {
    collection: firings,
    name: FIRINGS_BY_SLOT_INDEX,
    key: { orgId: 1, scheduleId: 1, slotKey: 1 },
    unique: true,
  },
  {
    collection: firings,
    name: FIRINGS_BY_SCHEDULE_INDEX,
    key: { orgId: 1, scheduleId: 1, slotAt: -1 },
  },
  {
    collection: firings,
    name: FIRINGS_PENDING_INDEX,
    key: { status: 1, leaseUntil: 1 },
  },
  {
    collection: firings,
    name: FIRINGS_WATCHED_INDEX,
    key: { status: 1, watchUntil: 1 },
  },
]);

/** The schedules' indexes alone, for an install that runs schedules without the harness (ADR-SCH-02 D1). */
export async function migrateScheduleStore(
  db: Db,
): Promise<IndexMigrationResult> {
  const results = [
    await SCHEDULES_MIGRATION.up(db),
    await TRIGGERS_MIGRATION.up(db),
  ];
  return {
    created: results.flatMap((r) => r.created),
    dropped: results.flatMap((r) => r.dropped),
  };
}

const forks = AgentSessionForkModel.collection.collectionName;

/** D2 for SS3: erasing either side of a fork finds its records by session. */
export const SESSION_FORKS_MIGRATION = indexMigration('session-forks', [
  {
    collection: forks,
    name: SESSION_FORKS_BY_SOURCE_INDEX,
    key: { orgId: 1, sourceSessionId: 1 },
  },
  {
    collection: forks,
    name: SESSION_FORKS_BY_FORK_INDEX,
    key: { orgId: 1, forkSessionId: 1 },
  },
]);

/**
 * D2 for DUR2: the sweeper's stranded-run scan (`strandedRuns`) reads queued
 * runs that need a worker feature, longest wait first. Partial on queued runs,
 * so terminal runs are never indexed.
 */
export const STRANDED_RUNS_MIGRATION = indexMigration('stranded-runs', [
  {
    collection: AgentRunModel.collection.collectionName,
    name: STRANDED_RUNS_INDEX,
    key: { 'requires.features': 1, 'waitingForWorker.since': 1 },
    partialFilterExpression: { status: 'queued' },
  },
]);

/**
 * D2 for TRIGGERS2: the run watch goes round admitted firings oldest-checked
 * first (TR-07), and a webhook's per-window delivery counts expire (TR-04).
 */
export const TRIGGERS_MIGRATION = indexMigration('triggers', [
  {
    collection: AgentScheduleFiringModel.collection.collectionName,
    name: FIRINGS_WATCH_ROUND_INDEX,
    key: { status: 1, watchCheckedAt: 1 },
  },
  {
    collection: AgentScheduleCounterModel.collection.collectionName,
    name: COUNTERS_EXPIRY_INDEX,
    key: { expiresAt: 1 },
    expireAfterSeconds: 0,
  },
]);

/** D2 for P3.2: the indexes the workspace store's uniqueness relies on. */
export const WORKSPACE_STORE_MIGRATION = indexMigration('workspace-store', [
  {
    collection: AgentWorkspaceManifestModel.collection.collectionName,
    name: 'org_workspace_revision',
    key: { orgId: 1, workspaceId: 1, revision: 1 },
    unique: true,
  },
  {
    collection: AgentWorkspaceKeyModel.collection.collectionName,
    name: 'org_session',
    key: { orgId: 1, sessionId: 1 },
    unique: true,
  },
]);

export type WorkspaceStoreMigration = IndexMigrationResult;

export const migrateWorkspaceStore = (
  db: Db,
): Promise<WorkspaceStoreMigration> => WORKSPACE_STORE_MIGRATION.up(db);

/** Keeps the documents, so rolling a release back loses no workspace data. */
export const revertWorkspaceStore = (
  db: Db,
): Promise<WorkspaceStoreMigration> => WORKSPACE_STORE_MIGRATION.down(db);

/** D2 for P6.4: one write lease per workspace rests on this unique index. */
export const WORKSPACE_WRITERS_MIGRATION = indexMigration('workspace-writers', [
  {
    collection: AgentWorkspaceWriterModel.collection.collectionName,
    name: 'org_workspace',
    key: { orgId: 1, workspaceId: 1 },
    unique: true,
  },
]);

/**
 * Every agent-harness Mongo migration, in the order `up` runs them; `down`
 * runs them in reverse. The harness schemas set `autoIndex: false`, so these
 * are the only builders of harness indexes: C-15 fails when a declared index
 * is missing here. A new collection or index gets a new entry, never an edit
 * to a shipped one.
 */
export const HARNESS_MIGRATIONS: readonly HarnessMigration[] = [
  CORE_STORE_MIGRATION,
  WORKSPACE_STORE_MIGRATION,
  ERASURE_STORE_MIGRATION,
  CHILD_RUNS_MIGRATION,
  WORKSPACE_WRITERS_MIGRATION,
  BUDGET_LEDGER_MIGRATION,
  NEXT_RUNS_MIGRATION,
  SESSION_FORKS_MIGRATION,
  SCHEDULES_MIGRATION,
  MEMORY_MIGRATION,
  STRANDED_RUNS_MIGRATION,
  ERASURE_BACKFILL_MIGRATION,
  MEMORY_SETTLEMENT_MIGRATION,
  AUDIT_PENDING_MIGRATION,
  AUDIT_QUERIES_MIGRATION,
  MEMORY_CORRECTIONS_MIGRATION,
  SANDBOX_SNAPSHOTS_MIGRATION,
  TRIGGERS_MIGRATION,
  PRIORITY_CLAIMS_MIGRATION,
  BUDGET_HOLDS_MIGRATION,
  AUDIT_SEARCH_MIGRATION,
  RUN_LISTS_MIGRATION,
  INBOX_MIGRATION,
];

export async function migrateHarnessIndexes(
  db: Db,
  migrations: readonly HarnessMigration[] = HARNESS_MIGRATIONS,
): Promise<IndexMigrationResult> {
  const created: string[] = [];
  for (const migration of migrations) {
    created.push(...(await migration.up(db)).created);
  }
  return { created, dropped: [] };
}

export async function revertHarnessIndexes(
  db: Db,
  migrations: readonly HarnessMigration[] = HARNESS_MIGRATIONS,
): Promise<IndexMigrationResult> {
  const dropped: string[] = [];
  for (const migration of [...migrations].reverse()) {
    dropped.push(...(await migration.down(db)).dropped);
  }
  return { created: [], dropped };
}
