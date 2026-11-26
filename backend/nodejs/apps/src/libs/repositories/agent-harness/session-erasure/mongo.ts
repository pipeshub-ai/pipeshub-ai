import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import type { Collection } from 'mongodb';
import { ClientSession } from 'mongoose';
import { HarnessRun } from '../../../../modules/agent-harness/domain/execution';
import {
  addCounts,
  assertErasureAdvance,
  ERASURE_STEPS,
  ErasureCounts,
  ErasureJob,
  ErasureRequest,
  ErasureStep,
  erasureJobId,
  requestsOrg,
} from '../../../../modules/agent-harness/domain/session-erasure';
import {
  ErasureJobStore,
  SessionRunLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { AgentRunModel } from '../execution/schema/execution.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';
import {
  AgentSessionErasureModel,
  SessionErasureDocument,
} from './schema/session-erasure.schema';

const toJob = (document: SessionErasureDocument): ErasureJob => ({
  orgId: document.orgId,
  sessionId: document.sessionId,
  reason: document.reason,
  by: document.by ?? null,
  status: document.status,
  step: document.step ?? null,
  counts: { ...document.counts },
  attempt: document.attempt,
  failures: document.failures,
  requestedAt: document.requestedAt,
  nextAttemptAt: document.nextAttemptAt,
  completedAt: document.completedAt ?? null,
  lastErrorType: document.lastErrorType ?? null,
});

/** The job's own position: a write by a holder that lost it matches nothing. */
const fenced = (
  job: ErasureJob,
): {
  _id: string;
  status: 'pending';
  attempt: number;
  step: ErasureStep | null;
} => ({
  _id: erasureJobId(job.orgId, job.sessionId),
  status: 'pending' as const,
  attempt: job.attempt,
  step: job.step,
});

/**
 * A pipeline upsert: due at DB time `now`, and nothing changes for a session that already has its erasure (a repeated
 * delete, a retention pass).
 */
const enqueuePipeline = (
  request: ErasureRequest,
  now: unknown,
): Array<Record<string, unknown>> => {
  // Every stored job has a status: a missing one is the upsert's insert. Not `$ifNull`, which would overwrite a
  // stored null (a retention job's `by`) with the new request's value.
  const inserting = { $eq: [{ $type: '$status' }, 'missing'] };
  const keep = (field: string, value: unknown): { $cond: unknown[] } => ({
    $cond: [inserting, value, `$${field}`],
  });
  return [
    {
      $set: {
        orgId: { $literal: request.orgId },
        sessionId: { $literal: request.sessionId },
        reason: keep('reason', { $literal: request.reason }),
        by: keep('by', { $literal: request.by }),
        status: keep('status', 'pending'),
        step: keep('step', ERASURE_STEPS[0]),
        counts: keep('counts', { $literal: {} }),
        attempt: keep('attempt', 0),
        failures: keep('failures', 0),
        requestedAt: keep('requestedAt', now),
        nextAttemptAt: keep('nextAttemptAt', now),
        completedAt: keep('completedAt', null),
        lastErrorType: keep('lastErrorType', null),
        holder: keep('holder', null),
        leaseExpiresAt: keep('leaseExpiresAt', null),
      },
    },
  ];
};

/** Erasure jobs in `agentSessionErasures`, leased and fenced on DB time. */
export class MongoErasureJobStore implements ErasureJobStore<Tx> {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async enqueue(
    request: ErasureRequest,
    txHandle?: Tx | ClientSession,
  ): Promise<boolean> {
    return (await this.enqueueMany([request], txHandle)) === 1;
  }

  async enqueueMany(
    requests: readonly ErasureRequest[],
    txHandle?: Tx | ClientSession,
  ): Promise<number> {
    if (requestsOrg(requests) === null) return 0;
    const tx = asMongoSession(txHandle);
    const now = this.time.expr();
    // The native driver: one `update` command of pipeline upserts, which Mongoose's bulkWrite would cast as documents.
    const jobs =
      AgentSessionErasureModel.collection as unknown as Collection<SessionErasureDocument>;
    const result = await jobs.bulkWrite(
      requests.map((request) => ({
        updateOne: {
          filter: { _id: erasureJobId(request.orgId, request.sessionId) },
          update: enqueuePipeline(request, now),
          upsert: true,
        },
      })),
      {
        ordered: false,
        ...(tx !== undefined ? { session: tx } : MAJORITY_WRITE),
      },
    );
    return result.upsertedCount;
  }

  async find(orgId: string, sessionId: string): Promise<ErasureJob | null> {
    const document = await AgentSessionErasureModel.findOne({
      _id: erasureJobId(orgId, sessionId),
    }).lean();
    return document === null ? null : toJob(document);
  }

  async claimDue(holder: string, leaseMs: number): Promise<ErasureJob | null> {
    const now = await this.time.read();
    const document = await AgentSessionErasureModel.findOneAndUpdate(
      {
        status: 'pending',
        nextAttemptAt: { $lte: now },
        $or: [{ leaseExpiresAt: null }, { leaseExpiresAt: { $lte: now } }],
      },
      {
        $inc: { attempt: 1 },
        $set: { holder, leaseExpiresAt: now + leaseMs },
      },
      { sort: { nextAttemptAt: 1 }, new: true, ...MAJORITY_WRITE },
    ).lean();
    return document === null ? null : toJob(document);
  }

  async advance(
    job: ErasureJob,
    counts: ErasureCounts,
    next: ErasureStep | null,
    leaseMs: number,
    txHandle?: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    assertErasureAdvance(job, next);
    const now = await this.time.read();
    const result = await AgentSessionErasureModel.updateOne(fenced(job), {
      $set: {
        step: next,
        status: next === null ? 'completed' : 'pending',
        counts: addCounts(job.counts, counts),
        failures: 0,
        lastErrorType: null,
        completedAt: next === null ? now : null,
        ...(next === null
          ? { holder: null, leaseExpiresAt: null }
          : { leaseExpiresAt: now + leaseMs }),
      },
    }).session(tx ?? null);
    return result.modifiedCount === 1;
  }

  async reschedule(
    job: ErasureJob,
    at: number,
    failure: { errorType: string } | null,
  ): Promise<boolean> {
    const result = await AgentSessionErasureModel.updateOne(fenced(job), {
      $set: {
        nextAttemptAt: at,
        holder: null,
        leaseExpiresAt: null,
        ...(failure !== null ? { lastErrorType: failure.errorType } : {}),
      },
      ...(failure !== null ? { $inc: { failures: 1 } } : {}),
    });
    // Matched, not modified: rescheduling to the same time again changes no field, and the job is still ours (QA13-F4).
    return result.matchedCount === 1;
  }

  async withdraw(job: ErasureJob): Promise<boolean> {
    const result = await AgentSessionErasureModel.deleteOne(
      fenced(job),
      MAJORITY_WRITE,
    );
    return result.deletedCount === 1;
  }

  async findMany(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, ErasureJob>> {
    if (sessionIds.length === 0) return new Map();
    const documents = await AgentSessionErasureModel.find({
      _id: {
        $in: sessionIds.map((sessionId) => erasureJobId(orgId, sessionId)),
      },
    })
      .read('primary')
      .lean();
    return new Map(
      documents.map((document) => [document.sessionId, toJob(document)]),
    );
  }

  now(): Promise<number> {
    return this.time.read();
  }
}

/**
 * Runs per batch of a session's runs: a few small fields each, so a session of thousands of runs is one round trip,
 * not the server's default first batch of 101 and a `getMore`.
 */
export const SESSION_RUNS_BATCH = 5_000;

/** A session's runs, read through the `org_session` index. */
export class MongoSessionRunLookup implements SessionRunLookup {
  async runsOf(
    orgId: string,
    sessionId: string,
  ): Promise<
    Array<Pick<HarnessRun, 'runId' | 'status' | 'revision' | 'harnessMajor'>>
  > {
    return AgentRunModel.find(
      { orgId, sessionId },
      {
        _id: 0,
        runId: 1,
        status: 1,
        revision: 1,
        harnessMajor: 1,
      },
    )
      .read('primary')
      .batchSize(SESSION_RUNS_BATCH)
      .lean();
  }

  /** The root harness-v2 runs `actorId` started in the session (SLASH2 `/usage`). */
  async ownRootRunsOf(
    orgId: string,
    sessionId: string,
    actorId: string,
  ): Promise<string[]> {
    const runs = await AgentRunModel.find(
      { orgId, sessionId, actorId, harnessMajor: 2, parent: null },
      { _id: 0, runId: 1 },
    )
      .read('primary')
      .lean<Array<{ runId: string }>>();
    return runs.map((run) => run.runId);
  }
}
