/**
 * R31GVISO-FU-3: a run its sandbox's memory limit ended (`out_of_memory`; under gVisor the whole sandbox ends) carries
 * the limit from the worker's transition to `run.failed`, the live `RUN_ERROR` frame and the saved outcome, so the
 * web app names the limit in the reader's language; Node's own English names it too.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { HarnessRun, transitionJournalEvents } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { commitRunBodySchema } from '../../../../src/modules/agent-harness/transport/schemas';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { failedOutcome } from '../../../../src/modules/agent-harness/projection/outcome-view';
import { childFailureMessage, memoryLimitText, runFailure } from '../../../../src/modules/agent-harness/projection/transcript';

const RUN = 'run-1';

const run = (): HarnessRun =>
  ({
    runId: RUN,
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: 'user-1',
    harnessMajor: 2,
    status: 'running',
    revision: 1,
    fence: 1,
  }) as unknown as HarnessRun;

function journal(failed: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>]> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }],
    ['run.failed', failed],
  ];
  return specs.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        type,
        schemaVersion: 1,
        critical: true,
        ts: '2026-11-05T12:00:00.000Z',
        writer: 'node',
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const commit = (detail: Record<string, unknown>) =>
  commitRunBodySchema.safeParse({
    commandId: 'c-1',
    attemptId: 'a-1',
    fence: 1,
    transition: { status: 'failed', errorType: 'out_of_memory', detail },
  });

describe('R32-OOM: an out-of-memory end names its limit', () => {
  it('OOM-N1: the worker’s transition carries the limit onto run.failed; a bad limit is refused', () => {
    expect(commit({ kind: 'fail', memoryLimitMb: 4096 }).success).to.equal(true);
    expect(commit({ kind: 'fail', memoryLimitMb: 0 }).success).to.equal(false);
    expect(commit({ kind: 'fail', memoryLimitMb: 1.5 }).success).to.equal(false);
    const [event] = transitionJournalEvents(run(), {
      status: 'failed',
      errorType: 'out_of_memory',
      detail: { kind: 'fail', memoryLimitMb: 4096 },
    });
    expect(event?.data).to.deep.equal({ errorType: 'out_of_memory', memoryLimitMb: 4096 });
  });

  it('OOM-N2: the saved failure, its outcome and the live RUN_ERROR carry the limit and Node’s words name it', () => {
    const events = journal({ errorType: 'out_of_memory', memoryLimitMb: 4096 });
    const transcript = runTranscript(events, RUN);
    expect(transcript).to.deep.include({ kind: 'failure', code: 'out_of_memory', memoryLimitMb: 4096 });
    expect((transcript as { message: string }).message).to.contain('ran out of memory (limit 4 GB)');
    expect(failedOutcome(transcript as { code: string; message: string; memoryLimitMb: number })).to.deep.include({
      kind: 'failed',
      reason: 'out_of_memory',
      memoryLimitMb: 4096,
    });
    const projector = new RunProjector({
      runId: RUN,
      sessionId: 'ses-1',
      conversationId: 'ses-1',
      title: 'q',
      goal: 'q',
      audience: 'owner',
    });
    const frames = [...projector.begin(), ...projector.project(events)];
    const error = frames
      .map((frame) => frame.frame as unknown as Record<string, unknown>)
      .find((data) => data['type'] === 'RUN_ERROR');
    expect(error).to.deep.include({ code: 'out_of_memory', details: { memoryLimitMb: 4096 } });
  });

  it('OOM-N3: without a limit the words stand alone; GB only for whole gigabytes', () => {
    const failure = runFailure({ end: { status: 'failed', errorType: 'out_of_memory' } } as Parameters<typeof runFailure>[0]);
    expect(failure.message).to.match(/^This task ran out of memory and was stopped\./);
    expect(failure).to.not.have.property('memoryLimitMb');
    expect(memoryLimitText(1536)).to.equal('1536 MB');
    expect(memoryLimitText(2048)).to.equal('2 GB');
  });

  it('OOM-N4: a sub-task that ran out of memory says so in English, never "an internal error"', () => {
    expect(childFailureMessage('failed', 'out_of_memory')).to.match(/^This task ran out of memory and was stopped\./);
  });
});
