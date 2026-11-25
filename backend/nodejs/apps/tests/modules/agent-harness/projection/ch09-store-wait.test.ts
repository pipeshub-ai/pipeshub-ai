import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  RunProjector,
  WAITING_STORE,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { runFailure } from '../../../../src/modules/agent-harness/projection/transcript';
import { Timeline } from '../../../../src/modules/agent-harness/projection/timeline';

/** CH-09: an engine run waiting out a file-storage outage says so, and the bound's failure says what to do. */

const RUN = 'run-ch09';
let seq = 0;
const event = (type: string, data: Record<string, unknown>): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: RUN, attemptId: `${RUN}.a1`,
    fence: 1, type, schemaVersion: 1, critical: true, ts: `2026-10-30T12:00:0${seq}.000Z`, writer: 'worker',
    visibility: 'owner', data,
  }) as JournalEvent;

const statuses = (frames: unknown[]): Array<Record<string, unknown>> =>
  frames
    .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
    .filter((f) => f['type'] === 'STATE_SNAPSHOT')
    .map((f) => f['snapshot'] as Record<string, unknown>);

describe('CH-09: the file-storage wait', () => {
  it('projects run.yielded{store_wait} as the waiting_store status with its notBefore', () => {
    seq = 0;
    const frames = new RunProjector({
      runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'q', audience: 'owner',
    }).project([
      event('run.claimed', { fence: 1 }),
      event('turn.started', { turn: 1 }),
      event('run.yielded', { notBefore: '2026-10-30T12:00:30.000Z', reason: 'store_wait' }),
    ]);
    const waits = statuses(frames).filter((s) => s['status'] === WAITING_STORE);
    expect(WAITING_STORE).to.equal('waiting_store');
    expect(waits.map((s) => s['retry_at'])).to.deep.equal(['2026-10-30T12:00:30.000Z']);
  });

  it('says a run whose storage never came back stopped, and may be tried again', () => {
    const timeline = { end: { status: 'failed', errorType: 'store_unavailable' }, approvals: [] } as unknown as Timeline;
    expect(runFailure(timeline).message).to.match(/storage/i).and.match(/try again/i);
  });
});
