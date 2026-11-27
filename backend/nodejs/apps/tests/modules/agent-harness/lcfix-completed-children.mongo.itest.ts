/**
 * LCFIX: `completedRootChildren`, the read admission's engine carry starts from, answers the same on MongoDB as
 * in memory: the session's completed children of root runs, latest first, nothing of another session or depth.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';

const run = (runId: string, extra: Partial<HarnessRun>): HarnessRun =>
  ({
    runId,
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: 'user-1',
    userMessageKey: `m-${runId}`,
    idempotencyKey: `k-${runId}`,
    payloadHash: 'h',
    harnessMajor: 2,
    profile: 'dispatched',
    status: 'completed',
    outcome: 'answer',
    revision: 1,
    fence: 1,
    attemptId: null,
    workerId: null,
    leaseExpiresAt: null,
    absoluteDeadlineAt: 9_999_999_999_999,
    checkpoint: null,
    createdAt: 1,
    updatedAt: 1,
    ...extra,
  }) as HarnessRun;

const child = (depth: number) => ({ parent: { runId: 'turn', invocationId: 'i', depth } });

const RUNS = [
  run('task-old', { ...child(1), updatedAt: 10 }),
  run('task-new', { ...child(1), updatedAt: 30 }),
  run('task-running', { ...child(1), status: 'running', updatedAt: 40 }),
  run('grandchild', { ...child(2), updatedAt: 50 }),
  run('turn', { updatedAt: 60 }),
  run('task-fork', { ...child(1), sessionId: 'ses-fork', updatedAt: 70 }),
];

describe('LCFIX: completedRootChildren on MongoDB and in memory', () => {
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  it('lists the session\'s completed children of root runs, latest first, on both stores', async () => {
    await AgentRunModel.insertMany(RUNS);
    const mongo = mongoBackend().executionStore;
    const memory = new InMemoryExecutionStore();
    const held = (memory as unknown as { runs: Map<string, HarnessRun> }).runs;
    for (const r of RUNS) held.set(JSON.stringify([r.orgId, r.runId]), r);
    for (const store of [mongo, memory]) {
      const found = await store.completedRootChildren('org-1', 'ses-1', 20);
      expect(found.map((r) => r.runId)).to.deep.equal(['task-new', 'task-old']);
      expect((await store.completedRootChildren('org-1', 'ses-1', 1)).map((r) => r.runId)).to.deep.equal([
        'task-new',
      ]);
    }
  });
});
