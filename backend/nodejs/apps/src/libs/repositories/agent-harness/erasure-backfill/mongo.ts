import { idOperator } from '../../id-filter-guard/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { inMongoTransaction } from '../durable-transaction/mongo';
import { ClientSession } from 'mongoose';
import {
  BackfillCursor,
  BackfillItem,
  BackfillItemState,
  BackfillMarker,
  BackfillPlan,
} from '../../../../modules/agent-harness/domain/erasure-backfill';
import { ErasureBackfillStore } from '../../../../modules/agent-harness/ports/erasure-backfill';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import {
  AgentErasureBackfillItemModel,
  AgentErasureBackfillMarkerModel,
  AgentErasureBackfillPlanModel,
  ErasureBackfillItemDocument,
  ErasureBackfillMarkerDocument,
  ErasureBackfillPlanDocument,
} from './schema/erasure-backfill.schema';
import { MongoTime, mongoServerTime } from '../time/mongo';

const toPlan = (document: ErasureBackfillPlanDocument): BackfillPlan => ({
  digest: document._id,
  orgFilter: document.orgFilter ?? null,
  operator: document.operator,
  createdAt: document.createdAt,
  state: document.state,
  orgs: document.orgs,
  items: document.items,
  cursor: {
    org: document.cursor.org,
    batch: document.cursor.batch,
    phase: document.cursor.phase,
  },
  staleReason: document.staleReason ?? null,
});

const toItem = (document: ErasureBackfillItemDocument): BackfillItem => ({
  digest: document.digest,
  orgId: document.orgId,
  sessionId: document.sessionId,
  batch: document.batch,
  fingerprint: document.fingerprint,
  by: document.by ?? null,
  updatedAt: document.updatedAt,
  counts: { ...document.counts },
  state: document.state,
});

const toMarker = (document: ErasureBackfillMarkerDocument): BackfillMarker => ({
  orgId: document._id,
  digest: document.digest,
  operator: document.operator,
  completedAt: document.completedAt,
  erased: document.erased,
  withdrawn: document.withdrawn,
  skipped: document.skipped,
});

const writeOptions = (
  tx?: ClientSession,
): { session: ClientSession } | typeof MAJORITY_WRITE =>
  tx !== undefined ? { session: tx } : MAJORITY_WRITE;

/** Backfill plans, items and markers in Mongo; the cursor moves by compare-and-set on its whole value. */
export class MongoErasureBackfillStore implements ErasureBackfillStore<Tx> {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  now(): Promise<number> {
    return this.time.read();
  }

  async plan(digest: string): Promise<BackfillPlan | null> {
    const document = await AgentErasureBackfillPlanModel.findById(digest)
      .read('primary')
      .lean();
    return document === null ? null : toPlan(document);
  }

  async createPlan(plan: BackfillPlan): Promise<BackfillPlan> {
    const { digest, ...rest } = plan;
    await AgentErasureBackfillPlanModel.updateOne(
      { _id: digest },
      { $setOnInsert: rest },
      { upsert: true, ...MAJORITY_WRITE },
    );
    return (await this.plan(digest)) as BackfillPlan;
  }

  async putItems(items: readonly BackfillItem[]): Promise<void> {
    if (items.length === 0) return;
    await AgentErasureBackfillItemModel.bulkWrite(
      items.map((item) => ({
        updateOne: {
          filter: { _id: `${item.digest}:${item.sessionId}` },
          update: { $setOnInsert: { ...item } },
          upsert: true,
        },
      })),
      { ordered: false, ...MAJORITY_WRITE },
    );
  }

  async setPlanState(
    digest: string,
    from: BackfillPlan['state'],
    to: BackfillPlan['state'],
    detail: { staleReason?: string } = {},
  ): Promise<boolean> {
    const result = await AgentErasureBackfillPlanModel.updateOne(
      { _id: digest, state: from },
      {
        $set: {
          state: to,
          ...(detail.staleReason !== undefined
            ? { staleReason: detail.staleReason }
            : {}),
        },
      },
      MAJORITY_WRITE,
    );
    return result.modifiedCount === 1;
  }

  async items(
    digest: string,
    afterSessionId: string | null,
    limit: number,
  ): Promise<BackfillItem[]> {
    const documents = await AgentErasureBackfillItemModel.find({
      digest,
      ...(afterSessionId !== null
        ? { sessionId: idOperator({ $gt: afterSessionId }) }
        : {}),
    })
      .sort({ sessionId: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return documents.map(toItem);
  }

  async batchItems(
    digest: string,
    orgId: string,
    batch: number,
  ): Promise<BackfillItem[]> {
    const documents = await AgentErasureBackfillItemModel.find({
      digest,
      orgId,
      batch,
    })
      .sort({ sessionId: 1 })
      .read('primary')
      .lean();
    return documents.map(toItem);
  }

  async moveCursor(
    digest: string,
    from: BackfillCursor,
    to: BackfillCursor,
    txHandle?: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    const result = await AgentErasureBackfillPlanModel.updateOne(
      {
        _id: digest,
        'cursor.org': from.org,
        'cursor.batch': from.batch,
        'cursor.phase': from.phase,
      },
      { $set: { cursor: { ...to } } },
      writeOptions(tx),
    );
    return result.modifiedCount === 1;
  }

  async setItemStates(
    digest: string,
    states: ReadonlyArray<{ sessionId: string; state: BackfillItemState }>,
    txHandle?: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    if (states.length === 0) return;
    await AgentErasureBackfillItemModel.bulkWrite(
      states.map(({ sessionId, state }) => ({
        updateOne: {
          filter: { _id: `${digest}:${sessionId}` },
          update: { $set: { state } },
        },
      })),
      { ordered: true, ...writeOptions(tx) },
    );
  }

  async marker(orgId: string): Promise<BackfillMarker | null> {
    const document = await AgentErasureBackfillMarkerModel.findById(orgId)
      .read('primary')
      .lean();
    return document === null ? null : toMarker(document);
  }

  async markers(): Promise<BackfillMarker[]> {
    const documents = await AgentErasureBackfillMarkerModel.find({})
      .sort({ _id: 1 })
      .read('primary')
      .lean();
    return documents.map(toMarker);
  }

  async putMarker(
    marker: BackfillMarker,
    txHandle?: Tx | ClientSession,
  ): Promise<boolean> {
    const tx = asMongoSession(txHandle);
    const { orgId, ...rest } = marker;
    // An upsert, not an insert: a duplicate key would abort the caller's transaction.
    const result = await AgentErasureBackfillMarkerModel.updateOne(
      { _id: orgId },
      { $setOnInsert: rest },
      { upsert: true, ...writeOptions(tx) },
    );
    return result.upsertedCount === 1;
  }
}

/** Reads the plan inside a durable transaction: refuses where the deployment cannot run one (not a replica set). */
export async function probeBackfillTransactions(): Promise<void> {
  await inMongoTransaction((session) =>
    AgentErasureBackfillPlanModel.findOne({}, { _id: 1 }, { session }).lean(),
  );
}
