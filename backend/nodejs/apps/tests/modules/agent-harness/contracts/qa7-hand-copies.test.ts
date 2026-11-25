import { expect } from 'chai';
import * as core from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import * as ext from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { AgentRunModel } from '../../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { requestNarrowing } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import { TERMINAL_RUN_STATUSES } from '../../../../src/modules/agent-harness/domain/execution';
import { KNOWN_WORKER_FEATURES } from '../../../../src/modules/agent-harness/domain/worker-compat';
import { commitRunSchema } from '../../../../src/modules/agent-harness/transport/schemas';

/**
 * QA-7 (Node half): what Node stores, accepts and decides by is the generated
 * enumeration. The Python half is tests/unit/contracts_harness/test_qa7_hand_copies.py,
 * the frontend's lib/contracts/__tests__/qa7-hand-copies.test.ts.
 */
const commit = (body: Record<string, unknown>) =>
  commitRunSchema.safeParse({
    params: { runId: 'run-1' },
    body: { commandId: 'c-1', attemptId: 'a-1', fence: 1, ...body },
  }).success;

const batched = (transition: Record<string, unknown>) => commit({ transition });

const legacy = (status: string, outcome?: string) =>
  commit({
    expectedRevision: 1,
    status,
    ...(outcome === undefined ? {} : { outcome }),
    events: [{ type: 'run_progress', data: {} }],
    outbox: [],
  });

const enumOf = (path: string): unknown[] => [
  ...(AgentRunModel.schema.path(path) as unknown as { options: { enum: unknown[] } }).options.enum,
];

describe('QA-7 hand copies (Node)', () => {
  it('QA7-1 the worker features Node knows are the contract’s', () => {
    expect(KNOWN_WORKER_FEATURES).to.equal(ext.KNOWN_WORKER_FEATURES);
  });

  it('QA7-2 the run document stores exactly the generated statuses and outcomes', () => {
    expect(enumOf('status')).to.deep.equal([...ext.EXECUTION_STATUSES]);
    expect(enumOf('outcome')).to.deep.equal([...ext.RUN_OUTCOMES, null]);
  });

  it('QA7-2 a worker commit accepts every run-document status but queued, and only those', () => {
    for (const status of ext.EXECUTION_STATUSES) {
      expect(batched({ status }), status).to.equal(true);
      const terminal = (ext.TERMINAL_EXECUTION_STATUSES as readonly string[]).includes(status);
      const needsEvent = terminal || status === 'needs_reconciliation';
      if (!needsEvent) expect(legacy(status), status).to.equal(status !== 'queued');
    }
    expect(batched({ status: 'quarantined' }), 'the fold’s status is not the document’s').to.equal(false);
  });

  it('QA7-2 a worker commit’s outcome is a generated run outcome', () => {
    for (const outcome of ext.RUN_OUTCOMES) {
      expect(batched({ status: 'running', outcome }), outcome).to.equal(true);
      expect(legacy('running', outcome), outcome).to.equal(true);
    }
    expect(batched({ status: 'running', outcome: 'answered' })).to.equal(false);
  });

  it('QA7-6 the terminal statuses are the generated ones', () => {
    expect([...TERMINAL_RUN_STATUSES].sort()).to.deep.equal([...ext.TERMINAL_EXECUTION_STATUSES].sort());
    for (const status of ext.TERMINAL_EXECUTION_STATUSES) {
      expect(ext.EXECUTION_STATUSES).to.include(status);
    }
  });

  it('QA7-8 payload_store_unavailable is a yield reason only Node writes', () => {
    expect(core.YIELD_REASONS).to.include('payload_store_unavailable');
    const notBefore = '2026-10-09T00:00:00.000Z';
    expect(
      batched({ status: 'queued', detail: { kind: 'yield', reason: 'payload_store_unavailable', notBefore } }),
    ).to.equal(false);
  });

  it('QA7-42 every effort a run may request narrows to a profile effort', () => {
    for (const profileEffort of core.REASONING_EFFORTS) {
      expect(core.REQUESTED_EFFORTS).to.include(profileEffort);
    }
    for (const requested of core.REQUESTED_EFFORTS) {
      const narrowed = requestNarrowing(requested)?.reasoningEffort;
      expect(core.REASONING_EFFORTS, requested).to.include(narrowed);
    }
    expect(requestNarrowing('minimal')?.reasoningEffort).to.equal('low');
  });
});
