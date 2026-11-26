import { describe, expect, it } from 'vitest';
import { RUN_STATUSES, SUSPEND_REASONS } from '@/lib/contracts/harness/generated/core/enums';
import type { SSEEngineProgressEvent } from '@/chat/types';
import { agentName, engineJob, engineJobFromTimeline, jobTitle } from '../engine-worker';
import { JOB_STATUSES, isLive, jobStatusOf, jobStatusOfWorker, jobStatusWord } from '../job-status';
import { ctl4, journalFrames, journalParts, plainText, words } from './fixtures';

const engineFrames = (parts: Array<{ type: string; frames?: never[] }>): SSEEngineProgressEvent[] =>
  (parts.find((p) => p.type === 'engine_task')?.frames ?? []) as SSEEngineProgressEvent[];

describe('a child engine run as a job', () => {
  it('the U3 engine run: plain steps in order, the ask it raised answered yes, its file, and Done', () => {
    const frames = engineFrames(journalParts('u3_engine_run').parts);
    const job = engineJob(
      {
        runId: 'run-u3-engine',
        engine: 'codex',
        status: 'completed',
        frames,
        approvals: [{ interactionId: 'ix.approve.k0f1e2d3c4b5a69788796a5b4c3d2e1f00112233', toolName: 'jira_create_issue', status: 'approved' }],
      },
      words,
    );
    expect(job.status).toBe('done');
    expect(job.worker.agent).toBe('Codex');
    expect(job.worker.title).toBe('Write the Q3 report and file it');
    expect(job.worker.steps.map((s) => [s.kind, s.title])).toEqual([
      ['command', 'Ran report.py'],
      ['file', 'Created out/q3-report.md'],
      ['approval', 'Jira create issue?'],
      ['command', 'Used Jira create issue'],
    ]);
    expect(job.worker.steps[0]).toMatchObject({ command: 'python report.py', activeTitle: 'Running report.py' });
    expect(job.worker.outputs).toEqual([{ name: 'q3-report.md', artifactId: 'art-q3' }]);
    expect(Object.values(job.state.answers)).toEqual(['yes']);
    expect(job.state.done).toBe(job.worker.steps.length);
    expect(job.worker.report.length).toBeGreaterThan(0);
  });

  it('the same run while its ask is open: Needs you, parked on the asking step', () => {
    const frames = engineFrames(journalParts('u3_engine_run').parts);
    const upToAsk = frames.slice(0, frames.findIndex((f) => f.kind === 'approval') + 1);
    const job = engineJob({ runId: 'r', engine: 'claude_code', status: 'suspended', frames: upToAsk }, words);
    expect(job.status).toBe('needsYou');
    expect(job.worker.agent).toBe('Claude Code');
    const parked = job.worker.steps[job.state.done];
    expect(parked).toMatchObject({ kind: 'approval', question: 'Allow Jira create issue?', approve: 'Allow', decline: "Don't allow" });
  });

  it('an ask nobody answered before the run ended is a no: silence is never a yes', () => {
    const frames = engineFrames(journalParts('u3_engine_run').parts);
    const job = engineJob({ runId: 'r', status: 'failed', frames }, words);
    expect(job.status).toBe('couldntFinish');
    expect(job.state.failed).toBe(true);
    expect(Object.values(job.state.answers)).toEqual(['no']);
  });

  it('reads the live frames the stream carried too (`CUSTOM engine_progress` values)', () => {
    const frames = journalFrames('u3_engine_run')
      .map((x) => ((x as { frame?: unknown }).frame ?? x) as { name?: string; value?: SSEEngineProgressEvent })
      .filter((f) => f.name === 'engine_progress')
      .map((f) => f.value as SSEEngineProgressEvent);
    const job = engineJob({ runId: 'r', status: 'running', frames: frames.slice(0, 2) }, words);
    expect(job.status).toBe('working');
    expect(job.worker.steps[job.state.done]).toMatchObject({ kind: 'command', command: 'python report.py' });
  });

  it('a real Codex run (CTL-4): every step a person reads is plain words, no ids or JSON', () => {
    const job = engineJob({ runId: '9d1315d0', engine: 'codex', status: 'completed', frames: engineFrames(ctl4().parts) }, words);
    expect(job.worker.steps.length).toBeGreaterThan(0);
    for (const step of job.worker.steps) {
      expect(plainText(step.title), step.title).toBe(true);
      expect(plainText(step.question), step.question).toBe(true);
    }
    expect(job.status).toBe('done');
  });

  it('from a child\'s timeline', () => {
    const job = engineJobFromTimeline(
      {
        runId: 'run-child',
        parentRunId: 'run-parent',
        status: 'cancelled',
        parts: [],
        engine: { taskId: 't', frames: [{ tool: 'code_task', taskId: 't', kind: 'started', text: 'Fix the flaky login test' }], truncated: false },
        approvals: [],
        children: [],
        merge: null,
        budget: null,
        codingEngine: 'opencode',
      },
      words,
    );
    expect(job).toMatchObject({ status: 'stopped', worker: { agent: 'OpenCode', title: 'Fix the flaky login test' }, state: { stopped: true } });
  });

  it('names the engine in words, and a job with no goal still has a title', () => {
    expect(agentName('loop')).toBe('Background agent');
    expect(agentName(undefined)).toBe('Background agent');
    expect(jobTitle('', words)).toBe('Background job');
    expect(jobTitle('Fix PAY-1423.', words)).toBe('Fix PAY-1423.');
  });

  it('names a job by its whole first line, the one name its card, ask, list and Jobs all use; a long goal is cut on a word', () => {
    expect(jobTitle('File the quarter ticket c4bcf4\ntool create_ticket {}', words)).toBe('File the quarter ticket c4bcf4');
    const long = `Compare ${'four competitors and their pricing pages '.repeat(4)}`;
    const title = jobTitle(long, words);
    expect(title.endsWith('…')).toBe(true);
    expect(title.length).toBeLessThanOrEqual(81);
    expect(long.startsWith(title.slice(0, -1))).toBe(true);
  });
});

describe('the six status words', () => {
  it('every contract run status maps to one of them, and suspended asks Needs you only for a person', () => {
    const map = Object.fromEntries(RUN_STATUSES.map((s) => [s, jobStatusOf(s)]));
    expect(map).toEqual({
      queued: 'upNext',
      running: 'working',
      suspended: 'working',
      completed: 'done',
      failed: 'couldntFinish',
      cancelled: 'stopped',
      quarantined: 'couldntFinish',
    });
    const suspended = Object.fromEntries(SUSPEND_REASONS.map((r) => [r, jobStatusOf('suspended', { suspendReason: r })]));
    expect(suspended).toMatchObject({ approval: 'needsYou', input: 'needsYou', timer: 'working', join: 'working', event: 'working' });
    expect(jobStatusOf('running', { openAsk: true })).toBe('needsYou');
  });

  it('are the design\'s words exactly', () => {
    expect(JOB_STATUSES.map((s) => jobStatusWord(s, words))).toEqual(['Up next', 'Working', 'Needs you', 'Done', "Couldn't finish", 'Stopped']);
  });

  it('the prototype\'s worker states fold into them; the live ones keep Stop offered', () => {
    expect(['queued', 'working', 'blocked', 'waiting', 'done', 'stopped', 'failed'].map((s) => jobStatusOfWorker(s as never))).toEqual([
      'upNext',
      'working',
      'working',
      'needsYou',
      'done',
      'stopped',
      'couldntFinish',
    ]);
    expect(JOB_STATUSES.filter(isLive)).toEqual(['upNext', 'working', 'needsYou']);
  });
});
