/**
 * DUR2 open item 4 on a real MongoDB replica set: the sweeper's stranded-run scan is
 * served by the index its migration builds, not by a scan of every queued run (run instructions in
 * execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  AgentRunModel,
  STRANDED_RUNS_INDEX,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { STRANDED_RUNS_MIGRATION } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
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

describe('DUR2 stranded-run scan index on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('answers the stranded-run query from queued_feature_waiting among many queued runs', async () => {
    const db = AgentRunModel.db.db;
    if (db === undefined) throw new Error('not connected');
    await STRANDED_RUNS_MIGRATION.up(db);
    const queued = Array.from({ length: 300 }, (_, i) => ({
      orgId: 'org-1',
      runId: `plain-${i}`,
      status: 'queued',
      waitingForWorker: null,
    }));
    const waiting = Array.from({ length: 5 }, (_, i) => ({
      orgId: 'org-1',
      runId: `code-${i}`,
      status: 'queued',
      requires: { features: ['agent_code'] },
      waitingForWorker: { since: 1_000 + i },
    }));
    await AgentRunModel.collection.insertMany([...queued, ...waiting]);

    const explained = await AgentRunModel.find({
      status: 'queued',
      'requires.features': 'agent_code',
      'waitingForWorker.since': { $ne: null, $lte: 10_000 },
    })
      .sort({ 'waitingForWorker.since': 1 })
      .limit(10)
      .explain('executionStats');
    const stats = (Array.isArray(explained) ? explained[0] : explained) as unknown as {
      queryPlanner: { winningPlan: unknown };
      executionStats: { totalDocsExamined: number; nReturned: number };
    };

    expect(usedIndexes(stats.queryPlanner.winningPlan)).to.include(STRANDED_RUNS_INDEX);
    expect(stats.executionStats.nReturned).to.equal(5);
    expect(stats.executionStats.totalDocsExamined).to.be.at.most(5);
  });
});
