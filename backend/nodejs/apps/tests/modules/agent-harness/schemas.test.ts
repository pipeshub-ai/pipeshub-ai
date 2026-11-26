import { expect } from 'chai';
import { commitRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const commit = (status: string, eventType?: string) => ({
  params: { runId: 'run-1' },
  body: {
    commandId: 'commit-1',
    attemptId: 'attempt-1',
    fence: 1,
    expectedRevision: 2,
    status,
    ...(status === 'completed' ? { outcome: 'answer' } : {}),
    events: eventType ? [{ type: eventType, data: {} }] : [],
    outbox: [],
  },
});

describe('agent harness transport schemas', () => {
  for (const [status, eventType] of [
    ['completed', 'run_completed'],
    ['failed', 'run_failed'],
    ['cancelled', 'run_cancelled'],
    ['needs_reconciliation', 'run_needs_reconciliation'],
  ]) {
    it(`requires ${eventType} when committing ${status}`, () => {
      expect(commitRunSchema.safeParse(commit(status)).success).to.equal(false);
      expect(
        commitRunSchema.safeParse(commit(status, eventType)).success,
      ).to.equal(true);
    });
  }

  it('permits a progress commit without a lifecycle terminal event', () => {
    expect(commitRunSchema.safeParse(commit('running')).success).to.equal(true);
  });
});
