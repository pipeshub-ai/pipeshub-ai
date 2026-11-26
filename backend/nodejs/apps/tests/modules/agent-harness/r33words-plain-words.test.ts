/**
 * R33-WORDS: the person always reads an honest, plain account of what happened.
 *
 * - R32REPO-FU-1 (ADR-RUN-03 D5): the answer's typed `limits` (what the run tried and could not do, from its journal)
 *   rides `x.pipeshub.answer.finalized` to the completion, its final part, the saved message and a shared viewer, as
 *   the not-found fact does; an item this build cannot word is dropped.
 * - R32SECCOMP-FU-4: a job its sandbox's memory limit ended names the limit on `child.completed`, its failure result
 *   and its `sub_agent` part, so its card reads "ran out of memory (limit 4 GB)" as the run's own error does.
 * - R32ENGFU-FU-1: a command whose engine can lose its output is marked on the activity view (its record, its live
 *   start, its engine line), so an empty output reads "No output was captured.", never "No output.".
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  childCompletedEvent,
  endingOf,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { sharedCompletion } from '../../../src/modules/agent-harness/projection/audiences';
import {
  ActivityFrame,
  applyLive,
  emptyActivity,
  foldActivity,
  mergeLive,
} from '../../../src/modules/agent-harness/projection/activity';
import { couldNotDoOf, foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts, childFailureMessage } from '../../../src/modules/agent-harness/projection/transcript';
import { buildAIResponseMessage } from '../../../src/modules/enterprise_search/utils/utils';
import { IAIResponse } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { ActivityJournal } from './support/activity-journal';

const RUN = 'run-r33w';
const LINE = "I couldn't read github.com/onyx-dot-app/onyx: the clone was declined.";
const ANSWER = `${LINE}\n\nOnyx's docs say it has 40 connectors.`;
const LIMITS = { items: [{ kind: 'repository.declined', repository: 'github.com/onyx-dot-app/onyx' }], line: LINE };

function event(seq: number, type: string, data: Record<string, unknown>, writer: 'node' | 'worker'): JournalEvent {
  return {
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq, runId: RUN,
    attemptId: writer === 'worker' ? `${RUN}.a1` : undefined, fence: writer === 'worker' ? 1 : undefined,
    type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-11-12T12:00:00.000Z', writer,
    visibility: 'owner', data,
  } as JournalEvent;
}

const journal = (answer: Record<string, unknown>): JournalEvent[] => [
  event(1, 'run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'),
  event(2, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'),
  event(3, 'x.pipeshub.answer.finalized', answer, 'worker'),
  event(4, 'run.completed', { outcome: String(answer['outcome']) }, 'node'),
];

const answered = (extra: Record<string, unknown> = {}) => ({
  outcome: 'answered', answer: ANSWER, partTexts: [ANSWER], citations: [], confidence: 'Low', ...extra,
});
const appended = (data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', runId: RUN, writer: 'worker',
    events: [{ type: 'x.pipeshub.answer.finalized', schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);
const VIEWS = {
  access: { citations: (c: unknown) => c, messages: (m: unknown) => m },
  artifacts: { card: (c: unknown) => c, text: (t: unknown) => t, messages: (m: unknown) => m, completion: (c: Record<string, unknown>) => c },
};

describe('R33-WORDS: what the run could not do, in the answer (R32REPO-FU-1)', () => {
  it('the contract takes the typed view and refuses a kind the chat cannot word or a model-written field', () => {
    expect(appended(answered({ limits: LIMITS }))).to.not.throw();
    expect(appended(answered({ limits: { items: [{ kind: 'web' }], line: LINE } }))).to.throw();
    expect(appended(answered({ limits: { items: [{ kind: 'repository.failed', why: 'the model said so' }], line: LINE } }))).to.throw();
    expect(appended(answered({ limits: { items: [], line: LINE } }))).to.throw();
  });

  it('rides the completion, its final part, the saved message and a shared viewer', () => {
    const timeline = foldTimeline(journal(answered({ limits: LIMITS })), RUN);
    const completion = buildCompletion(timeline)!;
    expect(completion['limits']).to.deep.equal(LIMITS);
    expect(buildParts(timeline).find((part) => part['isFinal'] === true)?.['limits']).to.deep.equal(LIMITS);
    const saved = buildAIResponseMessage({ statusCode: 200, data: { ...completion, citations: [] } as unknown as IAIResponse });
    expect(saved).to.deep.include({ limits: LIMITS });
    const seen = sharedCompletion(completion, VIEWS.access as never, VIEWS.artifacts as never);
    expect(seen['limits']).to.deep.equal(LIMITS);
  });

  it('drops what this build cannot word, and is absent when nothing was met', () => {
    expect(couldNotDoOf({ limits: { items: [{ kind: 'web' }, LIMITS.items[0]], line: LINE } })).to.deep.equal({ limits: LIMITS });
    expect(couldNotDoOf({ limits: { items: [{ kind: 'web' }], line: LINE } })).to.deep.equal({});
    expect(couldNotDoOf({ limits: { items: LIMITS.items } })).to.deep.equal({});
    expect(buildCompletion(foldTimeline(journal(answered()), RUN))).to.not.have.property('limits');
  });
});

describe('R33-WORDS: a job that ran out of memory names its limit (R32SECCOMP-FU-4)', () => {
  it('child.completed, its failure result and the sub_agent part carry the limit', () => {
    const ending = endingOf({ status: 'failed', errorType: 'out_of_memory', detail: { kind: 'fail', memoryLimitMb: 4096 } });
    expect(ending.memoryLimitMb).to.equal(4096);
    const completed = childCompletedEvent({ runId: 'run-child' }, ending);
    expect(completed.data).to.deep.include({ outcome: 'failed', errorType: 'out_of_memory', memoryLimitMb: 4096 });
    const ref = (completed.data['resultRef'] as { ref: string }).ref;
    const document = JSON.parse(Buffer.from(ref.slice('inline:'.length), 'base64url').toString('utf8')) as Record<string, unknown>;
    expect(document).to.deep.include({ error: 'out_of_memory', memoryLimitMb: 4096, success: false });

    const other = childCompletedEvent({ runId: 'run-child' }, endingOf({ status: 'failed', errorType: 'crash_loop', detail: { kind: 'fail', memoryLimitMb: 4096 } }));
    expect(other.data).to.not.have.property('memoryLimitMb');

    const parent = [
      event(1, 'run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'),
      event(2, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'),
      event(3, 'child.spawned', { childRunId: 'run-child', invocationId: 't1.0', grantDigest: `sha256:${'a'.repeat(64)}` }, 'node'),
      event(4, 'child.completed', completed.data as Record<string, unknown>, 'node'),
    ];
    const part = buildParts(foldTimeline(parent, RUN)).find((p) => p['type'] === 'sub_agent');
    expect(part).to.deep.include({ status: 'failed', errorType: 'out_of_memory', memoryLimitMb: 4096 });
    // The parent stream's live end of the job says it too (its RUN_ERROR's message and details).
    expect(childFailureMessage('failed', 'out_of_memory', 4096)).to.contain('ran out of memory (limit 4 GB)');
    expect(childFailureMessage('failed', 'out_of_memory')).to.not.contain('limit');
  });
});

describe('R33-WORDS: an engine that can lose a command’s output marks it (R32ENGFU-FU-1)', () => {
  const frame = (n: number, kind: ActivityFrame['kind'], data: Record<string, unknown>): ActivityFrame =>
    ({ runId: 'run-ux5', fence: 1, modelAttempt: 0, n, kind, data });

  it('from its record, its engine line and its live start', () => {
    const recorded = new ActivityJournal().commandFinished({ commandId: 'c1', source: 'engine', command: 'head -n 4 SKILL.md', outputMayBeLost: true });
    const [fromRecord] = foldActivity(recorded.events, { runId: recorded.runId, runStatus: 'running' }).commands;
    expect(fromRecord).to.deep.include({ commandId: 'c1', outputMayBeLost: true });

    const lined = new ActivityJournal().engine('command', 'head -n 4 SKILL.md', { callId: 'c2', outputMayBeLost: true });
    const [fromLine] = foldActivity(lined.events, { runId: lined.runId, runStatus: 'running' }).commands;
    expect(fromLine).to.deep.include({ commandId: 'c2', outputMayBeLost: true });

    const live = applyLive(emptyActivity('run-ux5'), frame(1, 'command_started', { commandId: 'c3', source: 'engine', command: 'ls', outputMayBeLost: true }));
    expect(live.commands[0]).to.deep.include({ commandId: 'c3', outputMayBeLost: true });
    const journaled = foldActivity(new ActivityJournal().engine('command', 'ls', { callId: 'c3' }).events, { runId: 'run-ux5', runStatus: 'running' });
    expect(mergeLive(journaled, live).commands[0]).to.deep.include({ outputMayBeLost: true });

    const plain = new ActivityJournal().commandFinished({ commandId: 'c4', source: 'engine', command: 'ls' });
    expect(foldActivity(plain.events, { runId: plain.runId, runStatus: 'running' }).commands[0]).to.not.have.property('outputMayBeLost');
  });
});
