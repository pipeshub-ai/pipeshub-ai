import { mongoTx } from '../durable-transaction/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import { RunKeyDigest } from '../../../../modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';
import { MongoHeadRevisions } from '../../../../modules/control-plane';
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
  withFeature,
} from '../../../../modules/agent-harness/domain/worker-compat';
import {
  AdmitChildCommand,
  ChildAdmission,
  ChildRunStore,
} from '../../../../modules/agent-harness/ports/child-runs';
import { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import { inMongoTransaction } from '../durable-transaction/mongo';
import { MongoBudgetLedger } from '../budget-ledger/mongo';
import {
  AgentEventCounterModel,
  AgentRunModel,
  runFromDocument,
} from '../execution/schema/execution.schema';
import { MongoJournalStore } from '../journal/mongo';
import { releaseParent } from '../person-wait/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';
import { withFeatureExpr } from '../worker-compat-expr/mongo';
import {
  RunInputSeal,
  UNSEALED_RUN_INPUT,
} from '../../../../modules/agent-harness/application/run-input-seal';

const TERMINAL = [...TERMINAL_RUN_STATUSES];

export interface ChildJoinDeps {
  journal: JournalStore<Tx>;
  dispatches: DispatchOutbox<Tx>;
  time: MongoTime;
}

/**
 * The parent's side of a child ending, in the transaction that ends it
 * (P6.1). It writes the parent document, as a `join` suspension does, so the
 * two serialize: a parent either sees the child ended (`JOIN_READY`) or is
 * woken by it, never neither.
 */
export async function joinParent(
  child: HarnessRun,
  ending: ChildEnding,
  now: number,
  deps: ChildJoinDeps,
  sessionHandle: Tx | ClientSession,
): Promise<void> {
  const session = asMongoSession(sessionHandle);
  const parentRef = childOf(child);
  if (parentRef === null) return;
  const updated = await AgentRunModel.findOneAndUpdate(
    { orgId: child.orgId, runId: parentRef.runId },
    [
      {
        $set: {
          children: {
            spawned: { $ifNull: ['$children.spawned', 0] },
            open: {
              $max: [0, { $subtract: [{ $ifNull: ['$children.open', 0] }, 1] }],
            },
          },
        },
      },
    ],
    { updatePipeline: true, returnDocument: 'after', session },
  ).lean();
  if (updated === null) return;
  const parent = runFromDocument(updated);
  if (TERMINAL_RUN_STATUSES.has(parent.status)) return;
  const wakes = joinWakes(parent, parent.children?.open ?? 0);
  await deps.journal.append(
    nodeJournalAppend(parent, [
      childCompletedEvent(child, ending),
      ...(wakes ? [childDoneResumedEvent()] : []),
    ]),
    mongoTx(session),
  );
  await releaseParent(child, parent, now, session, wakes);
  if (!wakes) return;
  const woken = await AgentRunModel.updateOne(
    {
      orgId: parent.orgId,
      runId: parent.runId,
      revision: parent.revision,
      status: 'paused',
    },
    [
      {
        $set: {
          status: 'queued',
          suspendedFor: null,
          workerId: null,
          attemptId: null,
          leaseExpiresAt: null,
          notBefore: deps.time.expr(),
          revision: { $add: ['$revision', 1] },
          updatedAt: deps.time.expr(),
        },
      },
    ],
    { updatePipeline: true, session },
  );
  if (woken.modifiedCount !== 1) {
    throw new Error('The joining parent moved inside its own transaction');
  }
  await deps.dispatches.enqueue(
    [
      {
        ...joinWakeCommand(parent.runId, child.runId),
        orgId: parent.orgId,
        createdAt: now,
      },
    ],
    mongoTx(session),
  );
}

export interface MongoChildRunStoreOptions {
  dispatches: DispatchOutbox<Tx>;
  time?: MongoTime;
  journal?: JournalStore<Tx>;
  /** P6.5: opens a child's budget account inside its admission. */
  budgets?: Pick<MongoBudgetLedger, 'admitChildIn'> | null;
  runKeys?: RunKeyDigest;
  /** Seals the child's query in its document (LCFIX); kept in plaintext when omitted. */
  inputs?: RunInputSeal;
}

/** The head rows a child's admission holds its pin to: stateless point reads. */
const HEADS = new MongoHeadRevisions();

/**
 * Child admission on a replica set: the parent's attempt is re-checked on DB
 * time, and the child, both streams' events, the parent's counters and
 * requirement, the session's feature and the wake commit together.
 */
export class MongoChildRunStore implements ChildRunStore {
  private readonly dispatches: DispatchOutbox<Tx>;
  private readonly time: MongoTime;
  private readonly journal: JournalStore<Tx>;
  private readonly budgets: Pick<MongoBudgetLedger, 'admitChildIn'> | null;
  private readonly runKeys: RunKeyDigest;
  private readonly inputs: RunInputSeal;

  constructor(options: MongoChildRunStoreOptions) {
    this.dispatches = options.dispatches;
    this.time = options.time ?? mongoServerTime;
    this.journal = options.journal ?? new MongoJournalStore(this.time);
    this.budgets = options.budgets ?? null;
    this.runKeys = options.runKeys ?? platformRunKeyDigest();
    this.inputs = options.inputs ?? UNSEALED_RUN_INPUT;
  }

  async admitChild(command: AdmitChildCommand): Promise<ChildAdmission> {
    return inMongoTransaction(async (session) => {
      const parentDocument = await AgentRunModel.findOne({
        orgId: command.orgId,
        runId: command.parentRunId,
        status: 'running',
        workerId: command.attempt.workerId,
        attemptId: command.attempt.attemptId,
        fence: command.attempt.fence,
        $expr: {
          $and: [
            { $gt: ['$leaseExpiresAt', this.time.expr()] },
            { $gt: ['$absoluteDeadlineAt', this.time.expr()] },
          ],
        },
      })
        .session(session)
        .lean();
      if (parentDocument === null) {
        throw new HarnessConflictError(
          'STALE_ATTEMPT',
          'Only a running attempt admits children',
        );
      }
      const parent = runFromDocument(parentDocument);
      const existing = await AgentRunModel.findOne({
        orgId: command.orgId,
        runId: command.childRunId,
      })
        .session(session)
        .lean();
      if (existing !== null) {
        const known = runFromDocument(existing);
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
        assertPinCurrent(command, await HEADS.heads(command.orgId, session));
      }
      const now = await this.time.read();
      if (this.budgets !== null && command.budget !== undefined) {
        await this.budgets.admitChildIn(
          session,
          command.orgId,
          command.childRunId,
          command.budget,
          now,
        );
      }
      const counter = await AgentEventCounterModel.findOneAndUpdate(
        { orgId: parent.orgId, sessionId: parent.sessionId },
        {
          $addToSet: {
            journalFeatures: { $each: sessionChildFeatures(command.roleModel) },
          },
        },
        {
          upsert: true,
          returnDocument: 'after',
          setDefaultsOnInsert: true,
          session,
        },
      ).lean();
      const child = childRunDocument(parent, command, now, this.runKeys);
      child.requires = {
        ...withFeature(
          admissionRequirements(child, counter.journalFeatures ?? []),
          ...childFeatures(command.pinned, command.roleModel),
        ),
        schemaVersion: command.pinned.spec.schemaVersion,
      };
      await AgentRunModel.create(
        [
          {
            ...(await this.inputs.seal(child)),
            activeSessionKey: null,
            agentDefinition: null,
            via: command.via,
            pinned: canonicalJson(command.pinned),
            ...(command.resolved !== undefined
              ? { resolved: canonicalJson(command.resolved) }
              : {}),
          },
        ],
        { session },
      );
      await this.journal.append(command.childJournal, mongoTx(session));
      await this.journal.append(command.parentJournal, mongoTx(session));
      await AgentRunModel.updateOne(
        { orgId: parent.orgId, runId: parent.runId },
        [
          {
            $set: {
              children: {
                spawned: {
                  $add: [{ $ifNull: ['$children.spawned', 0] }, 1],
                },
                open: { $add: [{ $ifNull: ['$children.open', 0] }, 1] },
              },
              requires: withFeatureExpr(
                ...parentFeatures(command.pinned, command.roleModel),
              ),
            },
          },
        ],
        { updatePipeline: true, session },
      );
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
        mongoTx(session),
      );
      return { run: child, created: true };
    });
  }

  async openChildren(
    orgId: string,
    parentRunId: string,
  ): Promise<HarnessRun[]> {
    const documents = await AgentRunModel.find({
      orgId,
      'parent.runId': parentRunId,
      status: { $nin: TERMINAL },
    })
      .sort({ createdAt: 1 })
      .read('primary')
      .lean();
    return documents.map((document) =>
      runFromDocument(document as unknown as Record<string, unknown>),
    );
  }

  async orphanedChildren(limit: number): Promise<HarnessRun[]> {
    const documents = await AgentRunModel.aggregate<Record<string, unknown>>([
      {
        $match: {
          'parent.runId': { $exists: true },
          status: { $nin: TERMINAL },
        },
      },
      { $sort: { createdAt: 1 } },
      {
        $lookup: {
          from: AgentRunModel.collection.name,
          let: { org: '$orgId', parentRunId: '$parent.runId' },
          pipeline: [
            {
              $match: {
                $expr: {
                  $and: [
                    { $eq: ['$orgId', '$$org'] },
                    { $eq: ['$runId', '$$parentRunId'] },
                  ],
                },
              },
            },
            { $project: { _id: 0, status: 1 } },
          ],
          as: 'parentRun',
        },
      },
      {
        $match: {
          $or: [
            { parentRun: { $size: 0 } },
            { 'parentRun.status': { $in: TERMINAL } },
          ],
        },
      },
      { $limit: Math.max(limit, 1) },
      { $project: { parentRun: 0 } },
    ]).read('primary');
    return documents.map(runFromDocument);
  }
}
