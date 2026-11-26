import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { MessagePart, PendingApproval } from '@/chat/types';
import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';
import { agentName } from '../engine-worker';
import { activityRows, changedFiles, machineLines } from '../job-activity';
import { briefHistoryView, briefView } from '../job-brief';
import { emptyJobStream, endsJobStream, jobStreamFrom, reduceJobFrame } from '../job-stream';
import { boardWords, bylineWords, cardWords, jobClock, jobRefsOfParts, jobView, operatorStopWords, reportsInFinishOrder, type JobRef } from '../jobs';
import { chatDot, finishedFolds, grantViews, jobRowOfPersonRun, jobsSections, requestView, rowMeta, statusOfPersonRun } from '../jobs-list';
import { jobBriefHistorySchema, type JobBriefHistory } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief-history';
import { requestJobsSchema, type RequestJobs } from '@/lib/contracts/harness/generated/ext/pipeshub/api/request-jobs';
import { ctl4, journalFrames, journalParts, plainText, REPO, words } from './fixtures';

const ENGINE = 'run-u3-engine';
const ASK = 'ix.approve.k0f1e2d3c4b5a69788796a5b4c3d2e1f00112233';
const frames = () => journalFrames('u3_engine_run');
const schemaExample = <T,>(name: string): T =>
  (JSON.parse(readFileSync(path.join(REPO, `contracts/harness/ext/pipeshub/api/${name}.schema.json`), 'utf8')) as { examples: T[] }).examples[0];

/** The recorded journal, with times on its first and last frames (the recording's own clock is a placeholder). */
function timed(list: unknown[], start: number, end: number): unknown[] {
  return list.map((f, i) => {
    const r = f as { id: string; frame: Record<string, unknown> };
    if (r.frame.type === 'RUN_STARTED') return { ...r, frame: { ...r.frame, timestamp: start } };
    if (i === list.length - 1) return { ...r, frame: { ...r.frame, timestamp: end } };
    return r;
  });
}

describe("a job's own stream (ADR-UI-01 D4), folded frame by frame", () => {
  it('the U3 engine run: every progress frame in order, its ask approved, Done, with the last id for a resume', () => {
    const state = jobStreamFrom(ENGINE, timed(frames(), 1000, 19_000));
    expect(state.ended).toBe(true);
    expect(state.status).toBe('completed');
    expect(state.frames.map((f) => f.kind)).toEqual(['started', 'command', 'output', 'command_done', 'file', 'version', 'diff', 'artifact', 'approval', 'tool', 'version', 'artifact', 'finished']);
    expect(state.asks).toEqual([expect.objectContaining({ interactionId: ASK, toolName: 'jira_create_issue', status: 'approved' })]);
    expect(state.startedAt).toBe(1000);
    expect(state.finishedAt).toBe(19_000);
    expect(state.lastEventId).toBe('j:28.4/0.0.0');
  });

  it('QA-AUTO F5: with no time on RUN_STARTED or RUN_FINISHED (as the durable stream sends them), the snapshots time the job', () => {
    const T0 = Date.parse('2026-11-10T09:00:00.000Z');
    const queued = { frame: { type: 'STATE_SNAPSHOT', runId: ENGINE, snapshot: { run_id: ENGINE, status: 'queued', started_at: new Date(T0 - 60_000).toISOString(), updated_at: new Date(T0 - 60_000).toISOString() } } };
    const recorded = frames().map((f) => {
      const r = f as { id: string; frame: Record<string, unknown> };
      const snapshot = r.frame.snapshot as Record<string, unknown> | undefined;
      if (r.frame.type !== 'STATE_SNAPSHOT' || !snapshot?.started_at) return r;
      const updated = snapshot.status === 'completed' ? T0 + 18_000 : T0;
      return { ...r, frame: { ...r.frame, snapshot: { ...snapshot, started_at: new Date(T0).toISOString(), updated_at: new Date(updated).toISOString() } } };
    });
    expect(recorded.some((f) => (f as { frame: { type: string; timestamp?: unknown } }).frame.type === 'RUN_STARTED' && (f as { frame: { timestamp?: unknown } }).frame.timestamp !== undefined)).toBe(false);
    const state = jobStreamFrom(ENGINE, [queued, ...recorded]);
    expect(state.startedAt).toBe(T0);
    expect(state.finishedAt).toBe(T0 + 18_000);
    // A frame's own time still wins where one is sent.
    expect(jobStreamFrom(ENGINE, timed(frames(), 1000, 19_000)).startedAt).toBe(1000);
  });

  it('parked on its ask: suspended with the ask pending; the answer sends it back to work', () => {
    const all = frames();
    const at = all.findIndex((f) => (f as { frame: { name?: string } }).frame.name === 'approval_requested');
    const parked = jobStreamFrom(ENGINE, all.slice(0, at + 1));
    expect(parked.status).toBe('suspended');
    expect(parked.asks[0].status).toBe('pending');
    const answered = reduceJobFrame(parked, all[at + 2]);
    expect(answered.status).toBe('running');
    expect(answered.asks[0].status).toBe('approved');
  });

  it('a resume after Last-Event-ID reads the same as one whole replay', () => {
    const all = frames();
    let resumed = jobStreamFrom(ENGINE, all.slice(0, 9));
    for (const f of all.slice(9)) resumed = reduceJobFrame(resumed, f);
    expect(resumed).toEqual(jobStreamFrom(ENGINE, all));
  });

  it('a stop ends it Stopped and closes an open ask (silence is never a yes); a failure keeps its code', () => {
    const parked = jobStreamFrom(ENGINE, frames().slice(0, 16));
    const stopped = reduceJobFrame(parked, { type: 'RUN_ERROR', runId: ENGINE, code: 'cancelled' });
    expect(stopped.status).toBe('cancelled');
    expect(stopped.asks.every((a) => a.status !== 'pending')).toBe(true);
    const failed = reduceJobFrame(emptyJobStream(ENGINE), { type: 'RUN_ERROR', runId: ENGINE, code: 'engine_crashed' });
    expect(failed).toMatchObject({ status: 'failed', errorCode: 'engine_crashed', ended: true });
  });

  it("ignores another run's frames and knows which frame ends the job's stream", () => {
    const other = reduceJobFrame(emptyJobStream(ENGINE), { type: 'RUN_FINISHED', runId: 'run-parent' });
    expect(other.ended).toBe(false);
    expect(endsJobStream({ type: 'RUN_FINISHED', runId: ENGINE }, ENGINE)).toBe(true);
    expect(endsJobStream({ type: 'RUN_FINISHED', runId: 'run-parent' }, ENGINE)).toBe(false);
  });
});

describe('jobs in the chat (plan flow 2)', () => {
  const parentParts = journalParts('u3_parent_with_engine_child').parts as MessagePart[];

  it("a turn's code_task hand-off is one job, with its goal; a role helper or an engine's own helper is not", () => {
    expect(jobRefsOfParts(parentParts)).toEqual([{ runId: 'run-u3-child', parentRunId: 'run-u3-parent', goal: 'Analyse in/q3.csv', partStatus: 'completed' }]);
    const helper = { type: 'sub_agent', runId: 'h1', roleName: 'research', status: 'completed', parts: [] } as MessagePart;
    expect(jobRefsOfParts([helper])).toEqual([]);
  });

  const ref: JobRef = { runId: ENGINE, goal: 'Write the Q3 report and file it', engine: 'claude_code', partStatus: 'running' };

  it('working on its own stream: the card says Working with a clock, the board says the step it is on', () => {
    const stream = jobStreamFrom(ENGINE, timed(frames().slice(0, 6), 1000, 1000));
    const job = jobView(ref, { stream }, [], words);
    expect(job).toMatchObject({ status: 'working', agent: 'Claude Code', title: 'Write the Q3 report and file it' });
    expect(cardWords(job, 65_000, words)).toBe('Working · 1m 04s');
    expect(boardWords(job, 9000, words)).toMatch(/ · 8s$/);
  });

  it('Working, then Needs you on its ask (naming its step), then Done with its file and report', () => {
    const all = timed(frames(), 1000, 19_000);
    const at = all.findIndex((f) => (f as { frame: { name?: string } }).frame.name === 'approval_requested');
    const parked = jobView(ref, { stream: jobStreamFrom(ENGINE, all.slice(0, at + 1)) }, [], words);
    expect(parked.status).toBe('needsYou');
    expect(cardWords(parked, 0, words)).toBe('Needs you');
    expect(parked.asking).toMatchObject({ doing: 'Jira create issue?' });
    const done = jobView({ ...ref, partStatus: 'completed' }, { stream: jobStreamFrom(ENGINE, all) }, [], words);
    expect(done.status).toBe('done');
    expect(cardWords(done, 0, words)).toBe('Done in 18s');
    expect(bylineWords(done, words)).toBe('finished in 18s');
    expect(done.outputs).toEqual([{ name: 'q3-report.md', artifactId: 'art-q3' }]);
    expect(done.report.length).toBeGreaterThan(0);
  });

  it("before its stream says anything, the chat's own open ask for it (childRunId) makes it Needs you", () => {
    const pending = [{ interactionId: 'ix-1', toolCallId: 'c', toolName: 'github__create_pull_request', args: '{}', argsSummary: null, status: 'pending', childRunId: ENGINE }] as PendingApproval[];
    expect(jobView(ref, {}, pending, words).status).toBe('needsYou');
    expect(jobView(ref, {}, [], words).status).toBe('working');
  });

  it('stopped by an admin says so with the reason, never the note (G11); a person stop is just Stopped', () => {
    const job = jobView({ ...ref, partStatus: 'cancelled' }, { operatorStop: { reason: 'maintenance' } }, [], words);
    expect(job.status).toBe('stopped');
    expect(job.stoppedBy).toBe('An admin stopped this: maintenance');
    expect(bylineWords(job, words)).toBe('An admin stopped this: maintenance');
    expect(operatorStopWords('unheard_of', words)).toBe('An admin stopped this: another reason');
    expect(cardWords(jobView({ ...ref, partStatus: 'cancelled' }, {}, [], words), 0, words)).toBe('Stopped');
  });

  it('a part saved as failed with cancelled was stopped, as its card reads it, not a failure', () => {
    expect(jobView({ ...ref, partStatus: 'failed', errorType: 'cancelled' }, {}, [], words).status).toBe('stopped');
    expect(jobView({ ...ref, partStatus: 'failed', errorType: 'engine_crashed' }, {}, [], words).status).toBe('couldntFinish');
  });

  it("couldn't finish is its own word", () => {
    const job = jobView({ ...ref, partStatus: 'failed' }, {}, [], words);
    expect(job.status).toBe('couldntFinish');
    expect(cardWords(job, 0, words)).toBe("Couldn't finish");
    expect(bylineWords(job, words)).toBe("couldn't finish");
  });

  it('reports land in finish order, whatever order the jobs were handed out', () => {
    const at = (runId: string, finishedAt?: number) => ({ ...jobView({ ...ref, runId, partStatus: 'completed' }, {}, [], words), ...(finishedAt ? { finishedAt } : {}) });
    const live = jobView({ ...ref, runId: 'live' }, {}, [], words);
    expect(reportsInFinishOrder([at('a', 30), live, at('b', 10), at('c')]).map((j) => j.runId)).toEqual(['b', 'a', 'c']);
  });

  it('the CTL-4 Codex run as a job: every word a person reads is plain', () => {
    const codexFrames = (ctl4().parts.find((p) => p.type === 'engine_task')?.frames ?? []) as never[];
    const stream = { ...emptyJobStream('9d1315d0'), status: 'completed' as const, ended: true, frames: codexFrames };
    const job = jobView({ runId: '9d1315d0', partStatus: 'completed', goal: 'Fix the failing test' }, { stream, engine: 'codex' }, [], words);
    expect(job.agent).toBe('Codex');
    for (const row of activityRows(job, words)) {
      if (row.kind === 'step') expect(plainText(row.title), row.title).toBe(true);
    }
  });

  it('names every engine the harness runs, ACP agents by their agent', () => {
    expect(['claude_code', 'codex', 'opencode', 'acp-gemini', 'acp-goose', 'acp-qwen', 'acp-dsh', 'acp-opencode', 'something'].map(agentName)).toEqual([
      'Claude Code',
      'Codex',
      'OpenCode',
      'Gemini',
      'Goose',
      'Qwen',
      'DeepSeek',
      'OpenCode',
      'Background agent',
    ]);
  });

  it('a clock as a person reads it', () => {
    expect([jobClock(4_000, words), jobClock(64_000, words), jobClock(600_000, words)]).toEqual(['4s', '1m 04s', '10m 00s']);
  });
});

describe("a job's Activity and Machine", () => {
  const ref: JobRef = { runId: ENGINE, goal: 'Write the Q3 report and file it', partStatus: 'completed' };
  const done = jobView(ref, { stream: jobStreamFrom(ENGINE, frames()) }, [], words);

  it('plain rows, the command under its row with output one click down, the ask as a stamp', () => {
    const rows = activityRows(done, words);
    expect(rows[0]).toMatchObject({ kind: 'step', title: 'Ran report.py', under: '$ python report.py', output: 'wrote out/q3-report.md\n' });
    const ask = rows.find((r) => r.kind === 'ask');
    expect(ask).toMatchObject({ stamp: true, live: false, text: 'Approved by you: jira create issue' });
    expect(ask && 'meta' in ask ? ask.meta : undefined).toBeUndefined();
    expect(changedFiles(done)).toEqual(['out/q3-report.md']);
  });

  it('while the ask waits, its row says to answer in the card over the chat', () => {
    const all = frames();
    const at = all.findIndex((f) => (f as { frame: { name?: string } }).frame.name === 'approval_requested');
    const parked = jobView({ ...ref, partStatus: 'running' }, { stream: jobStreamFrom(ENGINE, all.slice(0, at + 1)) }, [], words);
    expect(activityRows(parked, words).find((r) => r.kind === 'ask')).toMatchObject({ live: true, stamp: false, text: 'Waiting for you: Jira create issue?', meta: 'Answer it in the card over the chat.' });
  });

  it('a test run is named for its tests, with how it came out under the command', () => {
    const frames = [
      { type: 'CUSTOM', name: 'engine_progress', value: { tool: 'code_task', taskId: 't', kind: 'command', text: 'cd payments && pytest tests/test_refunds.py -q', data: { callId: 'c1' } } },
      { type: 'CUSTOM', name: 'engine_progress', value: { tool: 'code_task', taskId: 't', kind: 'output', text: 'F.\n1 failed, 1 passed in 0.42s', data: { callId: 'c1', stream: 'stdout' } } },
      { type: 'CUSTOM', name: 'engine_progress', value: { tool: 'code_task', taskId: 't', kind: 'command_done', text: '', data: { callId: 'c1', exitCode: 1, status: 'failed' } } },
    ];
    const job = jobView({ ...ref, runId: 't', partStatus: 'completed' }, { stream: jobStreamFrom('t', frames) }, [], words);
    expect(activityRows(job, words)[0]).toMatchObject({ title: 'Ran the refund tests', under: '$ cd payments && pytest tests/test_refunds.py -q', result: '1 failed' });
  });

  it('a stopped job ends its log with who stopped it', () => {
    const job = jobView({ ...ref, partStatus: 'cancelled' }, { stream: jobStreamFrom(ENGINE, frames().slice(0, 6)), operatorStop: { reason: 'cost' } }, [], words);
    expect(activityRows(job, words).at(-1)).toMatchObject({ kind: 'ended', label: 'An admin stopped this: cost' });
  });

  it('Machine is its terminal: each command, then what it printed', () => {
    expect(machineLines(jobStreamFrom(ENGINE, frames()).frames, true, words)).toEqual([
      { kind: 'command', text: '$ python report.py' },
      { kind: 'output', text: 'wrote out/q3-report.md' },
    ]);
  });
});

describe("a job's brief (ADR-UI-01 D3), worded", () => {
  const brief = schemaExample<JobBrief>('job-brief');

  it('the contract example: what was sent, what was kept back and why, what it can reach; no id, no raw tool name', () => {
    const view = briefView(brief, words);
    expect(view.goal).toMatch(/^Fix PAY-1423/);
    expect(view.sent.map((r) => r.text)).toEqual(['1 file from this chat', '2 sources PipesHub had read']);
    expect(view.withheld.map((r) => [r.text, r.note])).toEqual([
      ['The rest of this chat', 'It was given only its task'],
      ['Passwords, keys and sign-ins', 'They stay in PipesHub, outside its sandbox'],
      ['Tools', 'Not handed to it: Send message'],
    ]);
    expect(view.reach.map((r) => r.text)).toEqual([
      "Only your company's allowed websites",
      'Its own sandbox, closed when it ends',
      'Tools: Github: Create pull request',
      '1 action allowed for this chat without asking',
      'Passwords and keys stay in PipesHub. None are sent in.',
    ]);
    for (const row of [...view.sent, ...view.withheld, ...view.reach]) expect(plainText(row.text), row.text).toBe(true);
  });

  it('a brief that sent nothing says it got only its task; an erased goal is null', () => {
    const bare = { ...brief, goal: null, sent: { ...brief.sent, files: [], sources: [] }, reach: { ...brief.reach, tools: [], internet: 'none' as const } };
    const view = briefView(bare, words);
    expect(view.goal).toBeNull();
    expect(view.sent.map((r) => r.text)).toEqual(['Only its task']);
    expect(view.reach[0].text).toBe('No websites');
  });

  it.each(['conversation', 'credentials', 'tools', 'knowledge', 'skills', 'internet', 'files'] as const)('words the withheld code %s', (what) => {
    const view = briefView({ ...brief, withheld: [{ what, reason: 'not_granted' }] }, words);
    expect(view.withheld[0].text.length).toBeGreaterThan(3);
  });
});

describe('the Jobs panel across chats, the sidebar dot, the chat grants', () => {
  const page = schemaExample<PersonRuns>('person-runs');

  it("the person list's states as the six words", () => {
    expect(page.runs.map(statusOfPersonRun)).toEqual(['needsYou', 'stopped']);
    expect(statusOfPersonRun({ state: 'working', status: 'queued' })).toBe('upNext');
    expect(statusOfPersonRun({ state: 'working', status: 'running' })).toBe('working');
    expect(statusOfPersonRun({ state: 'finished', status: 'completed' })).toBe('done');
    expect(statusOfPersonRun({ state: 'failed', status: 'failed' })).toBe('couldntFinish');
  });

  it("rows named by the list's own title (UI-B5 D7), else by their agent; an admin stop keeps its reason", () => {
    const titled = { ...page.runs[0], title: 'Fix the failing payment test in the checkout service' };
    const [job, stopped] = [titled, page.runs[1]].map((r) => jobRowOfPersonRun(r, words));
    expect(job).toMatchObject({ title: 'Fix the failing payment test in the checkout service', agent: 'Claude Code', status: 'needsYou', chatTitle: 'Fix PAY-1423' });
    expect(stopped.stoppedBy).toBe('An admin stopped this: maintenance');
    expect(jobRowOfPersonRun(page.runs[0], words).title).toBe('Claude Code');
    expect(jobRowOfPersonRun({ ...page.runs[0], engine: null }, words).title).toBe(words('ui.job.untitled'));
  });

  it('sections in the design order, jobs of one request nested under it, cleared finished ones left out', () => {
    const base = jobRowOfPersonRun(page.runs[0], words);
    const rows = [
      { ...base, runId: 'a', status: 'done' as const, parentRunId: 'p1' },
      { ...base, runId: 'b', status: 'working' as const, parentRunId: 'p1' },
      { ...base, runId: 'c', status: 'working' as const, parentRunId: 'p1' },
      { ...base, runId: 'd', status: 'needsYou' as const, parentRunId: 'p2' },
      { ...base, runId: 'e', status: 'upNext' as const, parentRunId: 'p3' },
      { ...base, runId: 'f', status: 'couldntFinish' as const, parentRunId: 'p4' },
    ];
    const sections = jobsSections(rows, words);
    expect(sections.map((s) => [s.label, s.count])).toEqual([
      ['Needs you', 1],
      ['Working', 2],
      ['Up next', 1],
      ['Finished', 2],
    ]);
    expect(sections[1].groups).toEqual([expect.objectContaining({ key: 'p1', title: 'Fix PAY-1423', rows: [expect.objectContaining({ runId: 'b' }), expect.objectContaining({ runId: 'c' })] })]);
    expect(finishedFolds(sections)).toBe(true);
    expect(finishedFolds(jobsSections(rows.filter((r) => r.status === 'done'), words))).toBe(false);
    expect(jobsSections(rows, words, new Set(['a', 'f'])).map((s) => s.id)).toEqual(['needsYou', 'working', 'upNext']);
    expect(rowMeta({ ...base, status: 'working', startedAt: 1000 }, 6000, words)).toBe('5s');
  });

  it('the sidebar dot: needs you, then working, then finished and not seen yet; nothing for a quiet chat', () => {
    const quiet = { activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null };
    expect(chatDot(undefined)).toBeNull();
    expect(chatDot(quiet)).toBeNull();
    expect(chatDot({ ...quiet, finishedUnseen: true })).toBe('finished');
    expect(chatDot({ ...quiet, runState: 'working', activeRun: { runId: 'r', status: 'running' } })).toBe('working');
    expect(chatDot({ ...quiet, runState: 'working', needsYou: true })).toBe('needsYou');
  });

  it("a chat's grants in words: the app, the action and where, who allowed it", () => {
    const [view] = grantViews(
      [{ grantId: `cg_${'a'.repeat(40)}`, toolId: 'slack@i/send_message', tool: { app: 'slack', name: 'send_message' }, destination: '#sales', sensitivity: 'public', grantedBy: 'u1', grantedByName: 'Ana', grantedAt: '2026-11-08T10:00:00Z' }],
      words,
      'en-US',
    );
    expect(view.title).toBe('Slack: Send message to #sales');
    expect(view.detail).toBe('Allowed by Ana on Nov 8');
    expect(view.declined).toBe(false);
  });

  it('R38-AUTHZ (ADR-GRANT-01 D9): a call a person declined for their runs lists as declined, by them', () => {
    const declined = { grantId: `cg_${'b'.repeat(40)}`, toolId: 'jira@i/create_issue', tool: { app: 'jira', name: 'create_issue' }, destination: null, sensitivity: 'private' as const, decision: 'declined' as const, grantedBy: 'u1', grantedByName: 'Ana', grantedAt: '2026-11-08T10:00:00Z' };
    const [byAna] = grantViews([declined], words, 'en-US');
    expect(byAna.declined).toBe(true);
    expect(byAna.detail).toBe('Declined by Ana on Nov 8');
    const [byMe] = grantViews([declined], words, 'en-US', { me: 'u1', shared: true });
    expect(byMe.detail).toBe('Declined by you on Nov 8');
    const [alone] = grantViews([declined], words, 'en-US', { me: 'u1', shared: false });
    expect(alone.detail).toBe('Declined on Nov 8');
  });
});

/* UI-ADOPT1: how a job's goal changed, and a request's own view (UI-B5, ADR-UI-01 D5, D6). */

describe('how a job changed (GET …/brief/history)', () => {
  const [history, erased] = (JSON.parse(readFileSync(path.join(REPO, 'contracts/harness/ext/pipeshub/api/job-brief-history.schema.json'), 'utf8')) as { examples: JobBriefHistory[] }).examples.map((e) => jobBriefHistorySchema.parse(e) as JobBriefHistory);

  it('starts with what it was given, then each change oldest first: your message, your choice worded from its key, a hand-on', () => {
    const view = briefHistoryView(history, words);
    expect(view.startedAs).toBe('Fix the failing payment test');
    expect(view.changes.map((c) => c.text)).toEqual([
      'You added: Keep the public API unchanged',
      'You chose to allow: Create pull request in GitHub',
      'Handed on to a new job: Also cover the refund path',
    ]);
    expect(view.changes.map((c) => c.icon)).toEqual(['chat', 'check_circle', 'redo']);
    expect(view.changes.every((c) => plainText(c.text))).toBe(true);
  });

  it('a declined choice says so; a choice on a tool nobody worded keeps its English; nothing changed says that', () => {
    const declined = briefHistoryView({ ...history, changes: [{ kind: 'choice', at: 1, decision: 'denied', tool: 'acme__frob', action: 'Frob', actionKey: { name: 'frob', verb: 'frob', app: null } }] }, words);
    expect(declined.changes[0]).toMatchObject({ icon: 'block', text: 'You chose to decline: Frob' });
    expect(briefHistoryView({ ...history, changes: [{ kind: 'replanned', at: 1, runId: 'r', goal: null }] }, words).changes[0].text).toBe('Handed on to a new job');
    const none = briefHistoryView(erased, words);
    expect(none).toEqual({ startedAs: null, changes: [] });
  });
});

describe("a request's own view (GET …/runs/{runId}/jobs)", () => {
  const example = (JSON.parse(readFileSync(path.join(REPO, 'contracts/harness/ext/pipeshub/api/request-jobs.schema.json'), 'utf8')) as { examples: RequestJobs[] }).examples[0];
  const req = requestJobsSchema.parse(example) as RequestJobs;

  it('what you asked, "N of M back" while they run, and each job a row with a replacement under the job it replaced', () => {
    const view = requestView(req, words);
    expect(view).toMatchObject({ ask: 'Compare four competitors\' pricing', progress: '1 of 2 back', ran: 'Ran 2 jobs', done: false });
    expect(view.rows.map((r) => [r.runId, r.depth, r.status, r.title])).toEqual([
      ['run_2', 0, 'done', 'Fix the failing payment test'],
      ['run_3', 1, 'working', 'Claude Code'],
    ]);
    expect(view.rows[0]).toMatchObject({ sessionId: 'ses_1', agent: 'Claude Code', startedAt: 1762678800000, endedAt: 1762679400000 });
  });

  it('all back reads so; a job whose earlier job is not in the list stays at the top level; an erased ask is null', () => {
    const done = requestView({ ...req, request: { ...req.request, state: 'finished', status: 'completed', ask: null }, back: 2, jobs: req.jobs.map((j) => ({ ...j, state: 'finished' as const, status: 'completed' as const, continuesRunId: j.continuesRunId ? 'elsewhere' : null })) }, words, 'triage');
    expect(done).toMatchObject({ ask: null, progress: 'All back', done: true });
    expect(done.rows.map((r) => r.depth)).toEqual([0, 0]);
    expect(done.rows[0].agentKey).toBe('triage');
  });
});
