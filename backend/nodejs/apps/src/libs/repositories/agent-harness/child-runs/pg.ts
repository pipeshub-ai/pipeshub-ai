import type { Tx } from '../../uow';
import type { Heads } from '../../../../modules/control-plane';
import { RunKeyDigest } from '../../../../modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';
import { assertPinCurrent } from '../../../../modules/agent-harness/domain/admission';
import { HarnessConflictError } from '../../../../modules/agent-harness/domain/errors';
import {
  assertChildBound,
  ChildEnding,
  childCompletedEvent,
  childDoneResumedEvent,
  childFeatures,
  childRunDocument,
  joinWakeCommand,
  joinWakes,
  parentFeatures,
  sessionChildFeatures,
} from '../../../../modules/agent-harness/domain/child-runs';
import {
  childOf,
  HarnessRun,
  nodeJournalAppend,
  TERMINAL_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/execution';
import { canonicalJson } from '../../../../modules/agent-harness/contracts/canonical-json';
import {
  admissionRequirements,
  requirementsOf,
  withFeature,
} from '../../../../modules/agent-harness/domain/worker-compat';
import {
  AdmitChildCommand,
  ChildAdmission,
  ChildRunStore,
} from '../../../../modules/agent-harness/ports/child-runs';
import type { ChildBudgetAccount } from '../../../../modules/agent-harness/ports/budget-ledger';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import {
  RunInputSeal,
  UNSEALED_RUN_INPUT,
} from '../../../../modules/agent-harness/application/run-input-seal';
import { PgHeadRevisions } from '../../../../modules/control-plane';
import { jobsUnit, type PgKernelTx } from '../pg-documents/pg';
import { PgJournalStore } from '../journal/pg';
import {
  after,
  bumpSessionCounter,
  inOrgRead,
  inOrgTransaction,
  insertRun,
  lockRun,
  runDocs,
  saveRun,
  toRun,
} from '../execution/pg-rows';
import { releaseParent } from '../person-wait/pg';
import { PgTime, pgServerTime } from '../time/pg';

const TERMINAL = [...TERMINAL_RUN_STATUSES];

export interface PgChildJoinDeps {
  journal: JournalStore<Tx>;
  dispatches: DispatchOutbox<Tx>;
}

/**
 * The parent's side of a child ending, in the unit that ends it (P6.1). It locks the parent row, as a `join`
 * suspension does, so the two serialize: a parent either sees the child ended (`JOIN_READY`) or is woken by it,
 * never neither.
 */
export async function joinParent(
  tx: Tx,
  k: PgKernelTx,
  child: HarnessRun,
  ending: ChildEnding,
  now: number,
  deps: PgChildJoinDeps,
): Promise<void> {
  const parentRef = childOf(child);
  if (parentRef === null) return;
  const locked = await lockRun(k, child.orgId, parentRef.runId);
  if (locked === null) return;
  const counted = locked.doc['children'] as
    { spawned?: number; open?: number } | null | undefined;
  const doc = {
    ...locked.doc,
    children: {
      spawned: counted?.spawned ?? 0,
      open: Math.max(0, (counted?.open ?? 0) - 1),
    },
  };
  await saveRun(k, doc);
  const parent = toRun(doc);
  if (TERMINAL_RUN_STATUSES.has(parent.status)) return;
  const wakes = joinWakes(parent, parent.children?.open ?? 0);
  await deps.journal.append(
    nodeJournalAppend(parent, [
      childCompletedEvent(child, ending),
      ...(wakes ? [childDoneResumedEvent()] : []),
    ]),
    tx,
  );
  await releaseParent(k, child, { doc, run: parent }, now, wakes);
  if (!wakes) return;
  // Locked since the first read: what it holds now is this unit's own writes.
  const current = (await lockRun(k, parent.orgId, parent.runId))?.doc;
  if (current === undefined || current['status'] !== 'paused')
    throw new Error('The joining parent moved inside its own transaction');
  await saveRun(k, {
    ...current,
    status: 'queued',
    suspendedFor: null,
    workerId: null,
    attemptId: null,
    leaseExpiresAt: null,
    notBefore: now,
    revision: parent.revision + 1,
    updatedAt: now,
  });
  await deps.dispatches.enqueue(
    [
      {
        ...joinWakeCommand(parent.runId, child.runId),
        orgId: parent.orgId,
        createdAt: now,
      },
    ],
    tx,
  );
}

/** Opens a child's budget account inside its admission's unit (P6.5). */
export interface ChildBudgetAdmission {
  admitChildIn(
    tx: Tx,
    orgId: string,
    childRunId: string,
    budget: ChildBudgetAccount,
    now: number,
  ): Promise<void>;
}

/** The org's and the deployment's control-plane revisions, read in the caller's unit. */
export interface UnitHeads {
  heads(orgId: string, tx?: Tx): Promise<Heads>;
}

export interface PgChildRunStoreOptions {
  dispatches: DispatchOutbox<Tx>;
  time?: PgTime;
  journal?: JournalStore<Tx>;
  budgets?: ChildBudgetAdmission | null;
  runKeys?: RunKeyDigest;
  inputs?: RunInputSeal;
  /** What a pinned child's admission holds its pin to (CPD-10). */
  heads?: UnitHeads;
}

/**
 * Child admission on PostgreSQL: the parent's attempt is checked on the locked parent row on database time, and the
 * child, both streams' events, the parent's counters and requirement, the session's feature and the wake commit in
 * one unit.
 */
export class PgChildRunStore implements ChildRunStore {
  private readonly dispatches: DispatchOutbox<Tx>;
  private readonly time: PgTime;
  private readonly journal: JournalStore<Tx>;
  private readonly budgets: ChildBudgetAdmission | null;
  private readonly runKeys: RunKeyDigest;
  private readonly inputs: RunInputSeal;
  private readonly heads: UnitHeads;

  constructor(options: PgChildRunStoreOptions) {
    this.dispatches = options.dispatches;
    this.time = options.time ?? pgServerTime;
    this.journal = options.journal ?? new PgJournalStore(this.time);
    this.budgets = options.budgets ?? null;
    this.runKeys = options.runKeys ?? platformRunKeyDigest();
    this.inputs = options.inputs ?? UNSEALED_RUN_INPUT;
    this.heads = options.heads ?? new PgHeadRevisions();
  }

  admitChild(command: AdmitChildCommand): Promise<ChildAdmission> {
    return inOrgTransaction(command.orgId, async (tx, k) => {
      const now = await this.time.now(k);
      const locked = await lockRun(k, command.orgId, command.parentRunId);
      const parentDoc = locked?.doc;
      if (
        parentDoc === undefined ||
        parentDoc['status'] !== 'running' ||
        parentDoc['workerId'] !== command.attempt.workerId ||
        parentDoc['attemptId'] !== command.attempt.attemptId ||
        parentDoc['fence'] !== command.attempt.fence ||
        !after(parentDoc['leaseExpiresAt'], now) ||
        !after(parentDoc['absoluteDeadlineAt'], now)
      ) {
        throw new HarnessConflictError(
          'STALE_ATTEMPT',
          'Only a running attempt admits children',
        );
      }
      const parent = toRun(parentDoc);
      const existing = await lockRun(k, command.orgId, command.childRunId);
      if (existing !== null) {
        const known = toRun(existing.doc);
        if (
          known.payloadHash !== command.requestHash ||
          known.parent?.runId !== parent.runId
        ) {
          throw new HarnessConflictError(
            'IDEMPOTENCY_CONFLICT',
            'This invocation already admitted a different child',
          );
        }
        return { run: known, created: false };
      }
      assertChildBound(parent, command.maxChildren);
      if (command.resolved !== undefined) {
        // As a root's admission: a control-plane write committed after the child's pin was read refuses it.
        assertPinCurrent(command, await this.heads.heads(command.orgId, tx));
      }
      if (this.budgets !== null && command.budget !== undefined) {
        await this.budgets.admitChildIn(
          tx,
          command.orgId,
          command.childRunId,
          command.budget,
          now,
        );
      }
      const counter = await bumpSessionCounter(
        k,
        parent.orgId,
        parent.sessionId,
        { features: sessionChildFeatures(command.roleModel) },
      );
      const child = childRunDocument(parent, command, now, this.runKeys);
      child.requires = {
        ...withFeature(
          admissionRequirements(child, counter.journalFeatures),
          ...childFeatures(command.pinned, command.roleModel),
        ),
        schemaVersion: command.pinned.spec.schemaVersion,
      };
      await insertRun(k, {
        ...(await this.inputs.seal(child)),
        activeSessionKey: null,
        agentDefinition: null,
        engineProfile: null,
        modelAccess: null,
        via: command.via,
        pinned: canonicalJson(command.pinned),
        ...(command.resolved !== undefined
          ? { resolved: canonicalJson(command.resolved) }
          : {}),
      });
      await this.journal.append(command.childJournal, tx);
      await this.journal.append(command.parentJournal, tx);
      const counted = parentDoc['children'] as
        { spawned?: number; open?: number } | null | undefined;
      await saveRun(k, {
        ...parentDoc,
        children: {
          spawned: (counted?.spawned ?? 0) + 1,
          open: (counted?.open ?? 0) + 1,
        },
        requires: withFeature(
          requirementsOf(parent),
          ...parentFeatures(command.pinned, command.roleModel),
        ),
      });
      await this.dispatches.enqueue(
        [
          {
            orgId: command.orgId,
            commandId: `child:${command.childRunId}`,
            runId: command.childRunId,
            kind: 'run_ready',
            createdAt: now,
          },
        ],
        tx,
      );
      return { run: child, created: true };
    });
  }

  openChildren(orgId: string, parentRunId: string): Promise<HarnessRun[]> {
    return inOrgRead(orgId, async (k) =>
      (
        await runDocs(
          k,
          `/* pipeshub:harness.runs_open_children */ SELECT doc FROM harness.runs
            WHERE org_id = $1 AND parent_run_id = $2 AND status <> ALL($3::text[]) ORDER BY created_at, run_id`,
          [orgId, parentRunId, TERMINAL],
        )
      ).map(toRun),
    );
  }

  /** Cross-org (the sweeper's): as the jobs role, every org's children whose parent ended or is gone. */
  orphanedChildren(limit: number): Promise<HarnessRun[]> {
    return jobsUnit(
      'agent-harness.orphaned-children',
      { write: false },
      async (k) =>
        (
          await runDocs(
            k,
            `/* pipeshub:harness.runs_orphaned_children */ SELECT c.doc FROM harness.runs c
             LEFT JOIN harness.runs p ON p.org_id = c.org_id AND p.run_id = c.parent_run_id
            WHERE c.parent_run_id IS NOT NULL AND c.status <> ALL($1::text[])
              AND (p.run_id IS NULL OR p.status = ANY($1::text[]))
            ORDER BY c.created_at, c.run_id LIMIT $2`,
            [TERMINAL, Math.max(limit, 1)],
          )
        ).map(toRun),
    );
  }
}
