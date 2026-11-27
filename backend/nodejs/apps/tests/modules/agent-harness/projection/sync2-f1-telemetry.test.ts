import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';

/** SYNC2-F1: a run admitted with `includeRetrievalContext` streams today's loop's measurement frames, owner only. */

const RUN = 'run-f1';
let seq = 0;
const event = (type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: RUN, attemptId: `${RUN}.a1`,
    fence: 1, type, schemaVersion: 1, critical: writer === 'node', ts: `2026-10-17T12:00:0${seq}.000Z`, writer,
    visibility: 'owner', data,
  }) as JournalEvent;

const retrieval = { schemaVersion: 1, seq: 1, source: 'prefetch', status: 'empty', records: [], knownRecordIds: [], cumulativeBlocks: 0, cumulativeRecords: 0 };
const usage = { schemaVersion: 1, llmCalls: 1, inputTokens: 10, outputTokens: 2, calls: [[10, 2, 0]], turns: 0, completionGateNudges: 0 };

const custom = (audience: Audience): Array<Record<string, unknown>> => {
  seq = 0;
  return new RunProjector({ runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'q', audience })
    .project([
      event('run.claimed', { fence: 1 }, 'node'),
      event('x.pipeshub.goal.presented', { description: 'q' }),
      event('x.pipeshub.telemetry.recorded', { name: 'retrieval_context', value: retrieval }),
      event('x.pipeshub.telemetry.recorded', { name: 'run_usage', value: usage }),
    ])
    .map((f) => f.frame)
    .filter((f) => f.type === 'CUSTOM' && ['retrieval_context', 'run_usage'].includes(String(f['name'])));
};

describe('SYNC2-F1: retrieval telemetry on a durable run', () => {
  it('shows each journaled frame to the owner as the CUSTOM frame today\'s loop sends', () => {
    expect(custom('owner')).to.deep.equal([
      { type: 'CUSTOM', name: 'retrieval_context', value: retrieval, runId: RUN },
      { type: 'CUSTOM', name: 'run_usage', value: usage, runId: RUN },
    ]);
  });

  it('never shows them to a viewer of a shared conversation', () => {
    expect(custom('shared')).to.deep.equal([]);
  });
});
