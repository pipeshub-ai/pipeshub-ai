/**
 * CJ-2 (CLASSIC-JOBS D2, D11, D12): the job card's status word and its second half, for every status and every stop
 * reason, from the parent's part, the job's stream and the one timeline read after it ended.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import type { SSEEngineProgressEvent } from '../../../../types';
import { emptyJobStream, jobStreamFrom } from '../data';
import { clockWords, doneLine, elapsedMs, jobCardState, secondLine, statusWord } from '../job-card-state';

const t = i18n.t.bind(i18n);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

const line = (kind: string, text = '', data: Record<string, unknown> = {}): SSEEngineProgressEvent => ({ tool: 'code_task', taskId: 'j', kind, text, data });
const custom = (name: string, value: Record<string, unknown>) => ({ type: 'CUSTOM', name, value });

const words = (input: Parameters<typeof jobCardState>[0]) => {
  const state = jobCardState(input);
  return { status: state.status, word: statusWord(state, input.operatorStop, t) };
};

describe('the six status words', () => {
  it('a queued job is Starting, getting its machine ready', () => {
    const state = jobCardState({ part: { status: 'running' }, stream: emptyJobStream('j'), isOwner: true });
    expect(state.status).toBe('upNext');
    expect(statusWord(state, null, t)).toBe('Starting');
    expect(secondLine(state, { now: null, frames: [] }, t)).toBe('Getting the machine ready');
  });

  it('a started job is Working, with what it does now', () => {
    const stream = jobStreamFrom('j', [{ type: 'RUN_STARTED', runId: 'j', timestamp: 1000 }]);
    const state = jobCardState({ part: { status: 'running' }, stream, isOwner: true });
    expect(state.status).toBe('working');
    expect(secondLine(state, { now: 'Running npm test', frames: [] }, t)).toBe('Running npm test');
  });

  it('an open ask on its stream, or one the chat drew for it, is Needs you, said in words', () => {
    const stream = jobStreamFrom('j', [
      { type: 'RUN_STARTED', runId: 'j' },
      custom('approval_requested', { interactionId: 'i1', toolName: 'mail__send_email', displayName: 'Mail · Send email' }),
    ]);
    const state = jobCardState({ part: { status: 'running' }, stream, isOwner: true });
    expect(state.status).toBe('needsYou');
    expect(secondLine(state, { now: null, frames: [] }, t)).toBe('Wants to use Mail · Send email');
    const asked = jobCardState({ part: { status: 'running' }, waiting: true, isOwner: true });
    expect(asked.status).toBe('needsYou');
    expect(secondLine(asked, { now: null, frames: [] }, t)).toBe('Has a question for you');
  });

  it('a settled ask goes back to Working', () => {
    const stream = jobStreamFrom('j', [
      { type: 'RUN_STARTED', runId: 'j' },
      custom('approval_requested', { interactionId: 'i1', toolName: 'x' }),
      custom('approval_resolved', { interactionId: 'i1', decision: 'approved' }),
    ]);
    expect(jobCardState({ part: { status: 'running' }, stream, isOwner: true }).status).toBe('working');
  });

  it('a done job says what it made, what it changed, or that it finished', () => {
    expect(words({ part: { status: 'completed' }, isOwner: true })).toEqual({ status: 'done', word: 'Done' });
    expect(doneLine([line('artifact', 'out/report-q3.pdf', { artifactId: 'a1' })], t)).toBe('Made report-q3.pdf');
    expect(doneLine([line('file', 'a.ts'), line('diff', 'a.ts'), line('file', 'b.ts')], t)).toBe('Changed 2 files');
    expect(doneLine([line('artifact', 'draft.md', { lifecycle: 'discarded' })], t)).toBe('Finished');
  });

  it('a failed job could not finish, with the plain cause', () => {
    const state = jobCardState({ part: { status: 'failed', errorType: 'agent_error' }, isOwner: true });
    expect(statusWord(state, null, t)).toBe("Couldn't finish");
    expect(state.failure).toBe('agent_error');
  });

  it('the stream says how it ended once it saw the end, whatever the part still says', () => {
    const stream = jobStreamFrom('j', [{ type: 'RUN_STARTED', runId: 'j' }, { type: 'RUN_FINISHED', runId: 'j' }]);
    expect(jobCardState({ part: { status: 'running' }, stream, isOwner: true }).status).toBe('done');
  });
});

describe('every stop says who or what stopped it (D12)', () => {
  it.each([
    ['you, the owner', { part: { status: 'failed' as const, errorType: 'cancelled' }, isOwner: true }, 'Stopped by you'],
    ['someone else, to a shared viewer', { part: { status: 'failed' as const, errorType: 'cancelled' }, isOwner: false }, 'Stopped'],
    ['an admin, with the reason', { part: { status: 'failed' as const, errorType: 'cancelled' }, isOwner: true, operatorStop: { reason: 'maintenance' } }, 'Stopped by your admin: maintenance'],
    ['an admin, a reason this build does not know', { part: { status: 'failed' as const, errorType: 'operator_stopped' }, isOwner: true, operatorStop: { reason: 'weird' } }, 'Stopped by your admin: another reason'],
    ['its budget', { part: { status: 'failed' as const, errorType: 'budget_exhausted' }, isOwner: true }, 'Stopped: out of budget'],
    ['its time limit', { part: { status: 'failed' as const, errorType: 'deadline_exceeded' }, isOwner: true }, 'Stopped: took too long'],
  ])('%s', (_case, input, expected) => {
    expect(words(input)).toEqual({ status: 'stopped', word: expected });
  });

  it('a stop on the stream reads the same as a stop on the part', () => {
    const stream = jobStreamFrom('j', [{ type: 'RUN_STARTED', runId: 'j' }, { type: 'RUN_ERROR', runId: 'j', code: 'cancelled' }]);
    expect(words({ part: { status: 'running' }, stream, isOwner: true })).toEqual({ status: 'stopped', word: 'Stopped by you' });
  });
});

describe('elapsed time', () => {
  it('runs from the stream until it ends, else the saved duration', () => {
    const live = jobStreamFrom('j', [{ type: 'RUN_STARTED', runId: 'j', timestamp: 1_000 }]);
    expect(elapsedMs(live, {}, 135_000)).toBe(134_000);
    const ended = jobStreamFrom('j', [{ type: 'RUN_STARTED', runId: 'j', timestamp: 1_000 }, { type: 'RUN_FINISHED', runId: 'j', timestamp: 9_000 }]);
    expect(elapsedMs(ended, {}, 999_999)).toBe(8_000);
    expect(elapsedMs(undefined, { durationMs: 4_000 }, 0)).toBe(4_000);
    expect(elapsedMs(undefined, {}, 0)).toBeNull();
  });

  it('reads as the chat reads a duration', () => {
    expect(clockWords(8_000, t)).toBe('8s');
    expect(clockWords(134_000, t)).toBe('2m 14s');
    expect(clockWords(64_000, t)).toBe('1m 04s');
  });
});
