/**
 * G-45 on a real MongoDB replica set: claim-next by priority class is served by the index its migration builds,
 * not by sorting every queued run in memory (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  AgentRunModel,
  CLAIM_BY_PRIORITY_INDEX,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { PRIORITY_CLAIMS_MIGRATION } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([AgentRunModel] as unknown as Array<Model<never>>);

function usedIndexes(plan: unknown): string[] {
  if (plan === null || typeof plan !== 'object') return [];
  const node = plan as Record<string, unknown>;
  const own = typeof node.indexName === 'string' ? [node.indexName] : [];
  return own.concat(
    ...Object.values(node).flatMap((value) =>
      Array.isArray(value) ? value.map(usedIndexes) : [usedIndexes(value)],
    ),
  );
}

describe('G-45 claim-by-priority index on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('GA-24: reads the highest class first from claim_by_priority among 500 queued background runs', async () => {
    const db = AgentRunModel.db.db;
    if (db === undefined) throw new Error('not connected');
    await PRIORITY_CLAIMS_MIGRATION.up(db);
    const background = Array.from({ length: 500 }, (_, i) => ({
      orgId: 'org-1',
      runId: `firing-${i}`,
      status: 'queued',
      harnessMajor: 2,
      priorityClass: 'background',
      priorityRank: 2,
      notBefore: 1_000 + i,
      createdAt: 1_000 + i,
    }));
    const person = {
      orgId: 'org-1',
      runId: 'turn-1',
      status: 'queued',
      harnessMajor: 2,
      priorityClass: 'interactive',
      priorityRank: 0,
      notBefore: 9_000,
      createdAt: 9_000,
    };
    await AgentRunModel.collection.insertMany([...background, person]);

    const explained = await AgentRunModel.find({
      status: 'queued',
      harnessMajor: 2,
      priorityRank: { $not: { $gte: 2 } },
    })
      .sort({ priorityRank: 1, notBefore: 1, createdAt: 1 })
      .limit(4)
      .explain('executionStats');
    const stats = (Array.isArray(explained) ? explained[0] : explained) as unknown as {
      queryPlanner: { winningPlan: unknown };
      executionStats: { totalDocsExamined: number; nReturned: number };
    };

    expect(usedIndexes(stats.queryPlanner.winningPlan)).to.include(CLAIM_BY_PRIORITY_INDEX);
    expect(stats.executionStats.nReturned).to.equal(1);
    expect(stats.executionStats.totalDocsExamined).to.be.at.most(1);
  });
});
