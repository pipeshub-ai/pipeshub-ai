import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import type { OrgDataKeys } from '../../../../modules/agent-harness/application/org-data-keys';
import mongoose, { ClientSession, Types } from 'mongoose';
import type {
  Db,
  Document as MongoDocument,
  Filter,
  UpdateFilter,
} from 'mongodb';
import { escapeRegExp } from '../../../../utils/escape-regexp';
import {
  HarnessRun,
  TERMINAL_RUN_STATUSES,
} from '../../../../modules/agent-harness/domain/execution';
import {
  ORG_ERASURE_STEPS,
  ORG_STORE_COVERAGE,
  PrincipalErasureJob,
  PrincipalErasureStep,
  USER_ERASURE_STEPS,
  orgErasureJobId,
  userErasureJobId,
} from '../../../../modules/agent-harness/domain/principal-erasure';
import {
  OrgDataStore,
  PrincipalErasureJobStore,
} from '../../../../modules/agent-harness/ports/principal-erasure';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import {
  AgentRunModel,
  runFromDocument,
} from '../execution/schema/execution.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';
import {
  AgentPrincipalErasureModel,
  PrincipalErasureDocument,
} from './schema/principal-erasure.schema';

const toJob = (document: PrincipalErasureDocument): PrincipalErasureJob => ({
  kind: document.kind,
  orgId: document.orgId,
  userId: document.userId ?? null,
  by: document.by,
  step: document.step,
  requestedAt: document.requestedAt,
  auditRetentionDays: document.auditRetentionDays,
  auditRemoveAfter: document.auditRemoveAfter ?? null,
  cursor: document.cursor ?? null,
  transfers: [...document.transfers],
  journaled: document.journaled,
  counts: { ...document.counts },
  extension: document.extension ?? null,
  failures: document.failures,
  nextAttemptAt: document.nextAttemptAt,
  lastErrorType: document.lastErrorType ?? null,
});

const jobId = (
  job: Pick<PrincipalErasureJob, 'kind' | 'orgId' | 'userId'>,
): string =>
  job.kind === 'org'
    ? orgErasureJobId(job.orgId)
    : userErasureJobId(job.orgId, job.userId ?? '');

/** `agentPrincipalErasures`: one job per deleted org or user (CLEANUP-G1, G2). */
export class MongoPrincipalErasureJobs implements PrincipalErasureJobStore<Tx> {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  now(): Promise<number> {
    return this.time.read();
  }

  async requestOrg(
    request: {
      orgId: string;
      by: string;
      requestedAt: number;
      auditRetentionDays: number;
      extension?: Readonly<Record<string, unknown>> | null;
    },
    txHandle: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    const written = await AgentPrincipalErasureModel.updateOne(
      { _id: orgErasureJobId(request.orgId) },
      {
        $setOnInsert: {
          kind: 'org',
          orgId: request.orgId,
          userId: null,
          by: request.by,
          step: ORG_ERASURE_STEPS[0],
          requestedAt: request.requestedAt,
          auditRetentionDays: request.auditRetentionDays,
          auditRemoveAfter: null,
          cursor: null,
          transfers: [],
          journaled: 0,
          counts: {},
          extension: request.extension ?? null,
          failures: 0,
          nextAttemptAt: request.requestedAt,
          lastErrorType: null,
        },
      },
      { upsert: true, session: tx },
    );
    return written.upsertedCount === 1;
  }

  async requestUser(
    request: {
      orgId: string;
      userId: string;
      by: string;
      requestedAt: number;
      transfers: PrincipalErasureJob['transfers'];
    },
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    // A user deleted again (re-invited, then deleted) adds to the waiting job rather than replacing it.
    await AgentPrincipalErasureModel.updateOne(
      { _id: userErasureJobId(request.orgId, request.userId) },
      {
        $setOnInsert: {
          kind: 'user',
          orgId: request.orgId,
          userId: request.userId,
          by: request.by,
          step: USER_ERASURE_STEPS[0],
          requestedAt: request.requestedAt,
          auditRetentionDays: 0,
          auditRemoveAfter: null,
          cursor: null,
          journaled: 0,
          counts: {},
          failures: 0,
          nextAttemptAt: request.requestedAt,
          lastErrorType: null,
        },
        $push: { transfers: { $each: request.transfers } },
      },
      { upsert: true, session: tx },
    );
  }

  async findOrg(orgId: string): Promise<PrincipalErasureJob | null> {
    const document = await AgentPrincipalErasureModel.findById(
      orgErasureJobId(orgId),
    )
      .read('primary')
      .lean();
    return document === null ? null : toJob(document);
  }

  async due(now: number, limit: number): Promise<PrincipalErasureJob[]> {
    const documents = await AgentPrincipalErasureModel.find({
      nextAttemptAt: { $lte: now },
    })
      .sort({ nextAttemptAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return documents.map(toJob);
  }

  async writeProgress(
    job: PrincipalErasureJob,
    expectedStep: PrincipalErasureStep,
    txHandle?: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    const written = await AgentPrincipalErasureModel.updateOne(
      { _id: jobId(job), step: expectedStep },
      {
        $set: {
          step: job.step,
          cursor: job.cursor,
          journaled: job.journaled,
          counts: job.counts,
          failures: job.failures,
          nextAttemptAt: job.nextAttemptAt,
          lastErrorType: job.lastErrorType,
          auditRemoveAfter: job.auditRemoveAfter,
          extension: job.extension,
        },
      },
      tx !== undefined ? { session: tx } : MAJORITY_WRITE,
    );
    return written.matchedCount === 1;
  }

  async remove(job: PrincipalErasureJob): Promise<void> {
    await AgentPrincipalErasureModel.deleteOne(
      { _id: jobId(job) },
      MAJORITY_WRITE,
    );
  }
}

/** The collections keyed by a string `_id` are typed as ObjectId-keyed by the driver's default document. */
const byId = (id: unknown): Filter<MongoDocument> => {
  const filter: Filter<MongoDocument> = {};
  (filter as Record<string, unknown>)['_id'] = id;
  return filter;
};

const pullOrg = (orgId: string): UpdateFilter<MongoDocument> => {
  const update: UpdateFilter<MongoDocument> = {};
  (update as Record<string, unknown>)['$pull'] = { orgs: { orgId } };
  return update;
};

const objectIdOrg = (orgId: string): Filter<MongoDocument> =>
  Types.ObjectId.isValid(orgId)
    ? { orgId: new Types.ObjectId(orgId) }
    : { _id: { $in: [] } };

/**
 * How each `org_stores` collection names its org. Most carry the org as the
 * string `orgId`; the conversation module's documents as an ObjectId; a few
 * only in their id.
 */
const ORG_FILTERS: Readonly<
  Record<string, (orgId: string) => Filter<MongoDocument>>
> = {
  chatSessions: objectIdOrg,
  chatSessionMessages: objectIdOrg,
  conversations: objectIdOrg,
  agentconversations: objectIdOrg,
  notifications: objectIdOrg,
  citations: (orgId) => ({ 'metadata.orgId': orgId }),
  cpOrgHeads: (orgId) => byId(orgId),
  agentAuditChainChecks: (orgId) => byId(orgId),
  agentErasureBackfillMarkers: (orgId) => byId(orgId),
  agentErasureBackfillPlans: (orgId) => ({ orgFilter: orgId }),
  agentAdmissionQuotas: (orgId) =>
    byId({ $regex: `^\\["[a-z]+","${escapeRegExp(orgId)}"` }),
  outbox_events: (orgId) => ({
    topic: 'agent-run-events',
    value: { $regex: `"orgId":"${escapeRegExp(orgId)}"` },
  }),
};

const SESSION_BATCH = 500;

const MISSING_DATABASE = 'The Mongo connection is not open';

/** The org's documents across the harness's collections (CLEANUP-G1). */
export class MongoOrgData implements OrgDataStore<Tx> {
  /** `keys`: the key ring whose cache the shred must drop too; absent, only the stored keys go. */
  constructor(private readonly keys?: Pick<OrgDataKeys, 'shred'>) {}

  private get db(): Db {
    const db = mongoose.connection.db;
    if (db === undefined) throw new Error(MISSING_DATABASE);
    return db;
  }

  async sessionsAfter(
    orgId: string,
    after: string | null,
    limit: number,
  ): Promise<string[]> {
    const above = after === null ? {} : { $gt: after };
    const ids = new Set<string>();
    const sessions = Types.ObjectId.isValid(orgId)
      ? await this.db
          .collection('chatSessions')
          .find(
            {
              orgId: new Types.ObjectId(orgId),
              ...(after !== null && Types.ObjectId.isValid(after)
                ? { _id: { $gt: new Types.ObjectId(after) } }
                : {}),
            },
            { projection: { _id: 1 } },
          )
          .sort({ _id: 1 })
          .limit(limit)
          .toArray()
      : [];
    for (const session of sessions) ids.add(String(session['_id']));
    // Sessions with no conversation document: runs, journals and fork copies name them. Each source sends one page
    // of ids after `after` in id order, never all of the org's: a page reads at most `limit` from each.
    const named = { orgId, sessionId: { ...above, $type: 'string' } };
    for (const name of ['agentEventCounters', 'agentWorkspaceKeys']) {
      // One document per session (unique `org_session`).
      const page = await this.db
        .collection(name)
        .find(named, { projection: { _id: 0, sessionId: 1 } })
        .sort({ sessionId: 1 })
        .limit(limit)
        .toArray();
      for (const document of page) ids.add(String(document['sessionId']));
    }
    // Many runs per session: their distinct sessions, a page of them.
    const runs = await this.db
      .collection('agentRuns')
      .aggregate<{ _id: string }>([
        { $match: named },
        { $sort: { orgId: 1, sessionId: 1 } },
        { $group: { _id: '$sessionId', first: { $first: '$sessionId' } } },
        { $sort: { _id: 1 } },
        { $limit: limit },
        { $project: { _id: 1 } },
      ])
      .toArray();
    for (const run of runs) ids.add(run._id);
    return [...ids]
      .filter((id) => after === null || id > after)
      .sort()
      .slice(0, limit);
  }

  async markSessionsDeleted(
    orgId: string,
    sessionIds: readonly string[],
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    if (!Types.ObjectId.isValid(orgId)) return;
    const ids = sessionIds
      .filter((id) => Types.ObjectId.isValid(id))
      .map((id) => new Types.ObjectId(id));
    if (ids.length === 0) return;
    const at = Date.now();
    await this.db.collection('chatSessions').updateMany(
      { _id: { $in: ids }, orgId: new Types.ObjectId(orgId) },
      [
        {
          $set: {
            isDeleted: true,
            erasureRequestedAt: { $ifNull: ['$erasureRequestedAt', at] },
          },
        },
      ],
      { session: tx },
    );
  }

  pendingSessionErasures(orgId: string): Promise<number> {
    return this.db
      .collection('agentSessionErasures')
      .countDocuments({ orgId, status: 'pending' });
  }

  async activeRuns(orgId: string): Promise<HarnessRun[]> {
    const documents = await AgentRunModel.find({
      orgId,
      status: { $nin: [...TERMINAL_RUN_STATUSES] },
    })
      .read('primary')
      .lean();
    return documents.map((document) =>
      runFromDocument(document as unknown as Record<string, unknown>),
    );
  }

  async eraseStores(orgId: string): Promise<Record<string, number>> {
    const counts: Record<string, number> = {};
    for (const [name, coverage] of Object.entries(ORG_STORE_COVERAGE)) {
      if (coverage !== 'org_stores') continue;
      const filter = (ORG_FILTERS[name] ?? ((id: string) => ({ orgId: id })))(
        orgId,
      );
      let removed = 0;
      for (;;) {
        // Batched by id, so a large org never holds one long delete.
        const batch = await this.db
          .collection(name)
          .find(filter, { projection: { _id: 1 } })
          .limit(SESSION_BATCH)
          .toArray();
        if (batch.length === 0) break;
        const result = await this.db
          .collection(name)
          .deleteMany(
            { _id: { $in: batch.map((d) => d['_id']) } },
            MAJORITY_WRITE,
          );
        removed += result.deletedCount;
      }
      if (name === 'agentErasureBackfillPlans') {
        // A plan over several orgs keeps its other orgs' summaries.
        const pulled = await this.db
          .collection(name)
          .updateMany({ 'orgs.orgId': orgId }, pullOrg(orgId), MAJORITY_WRITE);
        removed += pulled.modifiedCount;
      }
      if (removed > 0) counts[name] = removed;
    }
    return counts;
  }

  async destroyDataKeys(orgId: string): Promise<number> {
    if (this.keys !== undefined) return this.keys.shred(orgId);
    const removed = await this.db
      .collection('agentHarnessDataKeys')
      .deleteMany({ orgId }, MAJORITY_WRITE);
    return removed.deletedCount;
  }

  async eraseAudit(orgId: string): Promise<number> {
    let removed = 0;
    for (const name of Object.keys(ORG_STORE_COVERAGE)) {
      if (ORG_STORE_COVERAGE[name] !== 'audit') continue;
      removed += (
        await this.db.collection(name).deleteMany({ orgId }, MAJORITY_WRITE)
      ).deletedCount;
    }
    return removed;
  }
}
