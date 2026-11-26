/**
 * OB-04, OB-05 (C9.04, C9.05) on a real MongoDB replica set: the run store's state the merged metrics route reads at
 * scrape time (runs by status, active sessions, expired leases, the oldest running run's age and, ADR-OPS-01 D10, the
 * longest wait for an approval), counted from runs that
 * have not ended only, each status query served by an index (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { MongoRunState } from '../../../src/libs/repositories/agent-harness/run-state/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([AgentRunModel] as unknown as Array<Model<never>>);

const NOW = 1_800_000_000_000;

const run = (runId: string, fields: Record<string, unknown>) => ({
  orgId: 'org-ob04',
  sessionId: `session-${runId}`,
  runId,
  createdAt: NOW - 60_000,
  ...fields,
});

describe('OB-04, OB-05: the run store state on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('counts the live runs by status, their sessions, expired leases and the oldest running age', async () => {
    await AgentRunModel.syncIndexes();
    await AgentRunModel.collection.insertMany([
      run('q1', { status: 'queued' }),
      run('q2', { status: 'queued', sessionId: 'session-shared' }),
      run('r1', { status: 'running', leaseExpiresAt: NOW + 5_000, createdAt: NOW - 90_000 }),
      run('r2', { status: 'running', leaseExpiresAt: NOW - 1, createdAt: NOW - 30_000, sessionId: 'session-shared' }),
      run('w1', { status: 'waiting_approval', updatedAt: NOW - 45_000 }),
      run('w2', { status: 'waiting_approval', updatedAt: NOW - 5_000, sessionId: 'session-r1' }),
      run('child', { status: 'running', leaseExpiresAt: NOW + 5_000, parent: { runId: 'r1' }, sessionId: 'session-r1' }),
      ...Array.from({ length: 50 }, (_, i) => run(`done-${i}`, { status: i % 2 ? 'completed' : 'failed' })),
    ]);
    const snapshot = await new MongoRunState(fixedMongoTime(() => NOW)).snapshot();
    expect(snapshot.byStatus).to.deep.equal({ queued: 2, running: 3, waiting_approval: 2 });
    // q1, w1, r1 (with its child and w2), and the session r2 and q2 share.
    expect(snapshot.activeSessions).to.equal(4);
    expect(snapshot.leaseExpired).to.equal(1);
    expect(snapshot.oldestRunningAgeS).to.equal(90);
    expect(snapshot.oldestApprovalWaitS, 'w1 has waited longest').to.equal(45);
  });

  it('answers an empty store with nothing running', async () => {
    await AgentRunModel.collection.insertOne(run('done', { status: 'completed' }));
    expect(await new MongoRunState(fixedMongoTime(() => NOW)).snapshot()).to.deep.equal({
      byStatus: {},
      activeSessions: 0,
      leaseExpired: 0,
      oldestRunningAgeS: null,
      oldestApprovalWaitS: null,
    });
  });

  it('reads the live runs through an index, never a scan of every run', async () => {
    await AgentRunModel.syncIndexes();
    const explained = (await AgentRunModel.find({ status: 'running', leaseExpiresAt: { $lt: NOW } })
      .explain('queryPlanner')) as unknown as Array<{ queryPlanner: { winningPlan: unknown } }> | { queryPlanner: { winningPlan: unknown } };
    const plan = JSON.stringify(Array.isArray(explained) ? explained[0]!.queryPlanner : explained.queryPlanner);
    expect(plan).to.include('IXSCAN');
    expect(plan).not.to.include('COLLSCAN');
  });
});
