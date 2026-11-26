import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  RunProjector,
  WAITING_SANDBOX,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { runFailure } from '../../../../src/modules/agent-harness/projection/transcript';
import { Timeline } from '../../../../src/modules/agent-harness/projection/timeline';

/** QA31F: an engine run waiting for a free sandbox says so, and the bound's failure says what the user can do. */

const RUN = 'run-qa31f';
let seq = 0;
const event = (type: string, data: Record<string, unknown>): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: RUN, attemptId: `${RUN}.a1`,
    fence: 1, type, schemaVersion: 1, critical: true, ts: `2026-10-13T12:00:0${seq}.000Z`, writer: 'node',
    visibility: 'owner', data,
  }) as JournalEvent;

const statuses = (frames: unknown[]): Array<Record<string, unknown>> =>
  frames
    .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
    .filter((f) => f['type'] === 'STATE_SNAPSHOT')
    .map((f) => f['snapshot'] as Record<string, unknown>);

describe('QA31F: the sandbox wait', () => {
  it('projects run.yielded{sandbox_wait} as the waiting_sandbox status with its notBefore', () => {
    seq = 0;
    const frames = new RunProjector({
      runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'q', audience: 'owner',
    }).project([
      event('run.claimed', { fence: 1 }),
      event('turn.started', { turn: 1 }),
      event('run.yielded', { notBefore: '2026-10-13T12:00:30.000Z', reason: 'sandbox_wait' }),
    ]);
    const waits = statuses(frames).filter((s) => s['status'] === WAITING_SANDBOX);
    expect(WAITING_SANDBOX).to.equal('waiting_sandbox');
    expect(waits.map((s) => s['retry_at'])).to.deep.equal(['2026-10-13T12:00:30.000Z']);
  });

  it('says a run that never got a sandbox did nothing and may be tried again', () => {
    const timeline = { end: { status: 'failed', errorType: 'sandbox_unavailable' }, approvals: [] } as unknown as Timeline;
    expect(runFailure(timeline).message).to.match(/sandbox/i).and.match(/try again/i);
  });
});
