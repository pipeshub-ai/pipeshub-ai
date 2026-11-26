/**
 * BGUI-6: finished jobs report back in the chat, in the order they finished (design §6B.42), under their cards and
 * before the turn's answer. BGUI-11: no report shows a job's own reply (the answer sums it up); one job always says
 * how it ended; several say it only when not plain done or when they have files. A partial is labelled partial, a
 * failure says why, a stop says who; delivered files are the answer's file cards, working files open its Outputs;
 * each job's card is still drawn once.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { MessagePart } from '../../../../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn(async () => undefined) }));

const { useChatStore } = await import('../../../../store');
const { TurnJobs } = await import('../turn-jobs');
const { useJobsStore } = await import('../data');
const { emptyJobStream } = await import('@/lib/harness-jobs/job-stream');
const { useActivityPanelStore } = await import('../../../activity-panel/activity-panel-store');
const { inFinishOrder, jobReportFacts } = await import('../job-report-state');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const job = (runId: string, patch: Partial<MessagePart> = {}): MessagePart => ({
  type: 'sub_agent',
  runId,
  parentRunId: 'run-root',
  codeTaskGoal: `Job ${runId}`,
  codeTaskHandoff: { trigger: 'upfront', reason: 'research', engine: 'claude_code' },
  status: 'completed',
  parts: [],
  ...patch,
});

const frame = (runId: string, kind: string, text: string, data: Record<string, unknown> = {}) => ({ tool: 'code_task', taskId: runId, kind, text, data });

/** A job whose stream the chat followed to its end. */
function ended(runId: string, opts: { at: number; startedAt?: number; finished?: string; artifact?: { name: string; id?: string }; status?: 'completed' | 'failed' | 'cancelled'; errorCode?: string }) {
  const frames = [
    ...(opts.artifact ? [frame(runId, 'artifact', opts.artifact.name, opts.artifact.id ? { artifactId: opts.artifact.id } : {})] : []),
    ...(opts.finished !== undefined ? [frame(runId, 'finished', opts.finished, { success: opts.status !== 'failed' })] : []),
  ];
  useJobsStore.setState((s) => ({
    streams: {
      ...s.streams,
      [runId]: {
        ...emptyJobStream(runId),
        status: opts.status ?? 'completed',
        frames,
        ended: true,
        startedAt: opts.startedAt ?? opts.at - 18_000,
        finishedAt: opts.at,
        ...(opts.errorCode ? { errorCode: opts.errorCode } : {}),
      },
    },
  }));
}

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

let timelines: Record<string, unknown>;
let requestJobs: unknown;

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, run: null, focusSection: null });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  timelines = {};
  requestJobs = null;
  apiClient.get.mockImplementation(async (url: string) => {
    const timeline = /\/runs\/([^/]+)\/timeline$/.exec(url)?.[1];
    if (timeline) {
      if (timelines[timeline]) return { data: timelines[timeline] };
      throw new Error('no timeline');
    }
    if (/\/runs\/run-root\/jobs$/.test(url) && requestJobs) return { data: requestJobs };
    if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (node: React.ReactElement) => render(h(Theme, null, node));
const reports = () => screen.queryAllByTestId('job-report');

describe('BGUI-6: one job says how it ended', () => {
  it('its byline names it and how it ended, never its reply, and its name opens its Brief', async () => {
    ended('job-1', { at: 1_000_000, finished: 'I found three late invoices and listed them.' });
    view(h(TurnJobs, { jobs: [job('job-1')] }));
    const report = await screen.findByTestId('job-report');
    expect(within(report).getByTestId('job-report-byline').textContent).toBe('Job job-1 · finished in 18s');
    expect(report.textContent).not.toContain('I found three late invoices');
    // The card is still the one drawing of the job: the report is a byline, not a second card.
    expect(screen.getAllByTestId('job-card')).toHaveLength(1);
    fireEvent.click(within(report).getByRole('button', { name: 'Job job-1' }));
    expect(useActivityPanelStore.getState().run?.runId).toBe('job-1');
    expect(useActivityPanelStore.getState().jobSection).toBe('brief');
  });

  it('a plain done still says how it ended: the one line under its card, its reply left to the answer', async () => {
    const words = 'The migration is done and the tests pass on both databases, so the release can go out today.';
    ended('job-1', { at: 1_000_000, finished: words });
    view(h(TurnJobs, { jobs: [job('job-1')] }));
    const report = await screen.findByTestId('job-report');
    expect(within(report).getByTestId('job-report-outcome').textContent).toBe(' · finished in 18s');
    expect(report.textContent).not.toContain('release can go out');
    expect(screen.getAllByTestId('job-card')).toHaveLength(1);
  });

  it('a job still going has no report: its card says how it is doing', () => {
    view(h(TurnJobs, { jobs: [job('job-1', { status: 'running' })] }));
    expect(reports()).toHaveLength(0);
  });
});

describe('BGUI-6: honest endings', () => {
  it('a budget-cut job is labelled partial, says which limit, and its saved file is the answer’s own file card', async () => {
    timelines['job-1'] = {
      runId: 'job-1',
      parentRunId: 'run-root',
      status: 'completed',
      parts: [{ type: 'text', isFinal: true, content: 'Here are the notes I had time for.', truncatedBy: 'budget' }],
      engine: null,
      approvals: [],
      children: [],
      merge: null,
      budget: null,
      digest: { outcome: 'answered', cutShortBy: 'budget', artifacts: [{ name: 'notes.md', recordId: 'rec-1' }], files: { added: 1, modified: 0, deleted: 0, working: 0 }, durationMs: 9_000 },
    };
    view(h(TurnJobs, { jobs: [job('job-1', { durationMs: 9_000 })] }));
    const report = await screen.findByTestId('job-report');
    expect(report.getAttribute('data-partial')).toBe('budget');
    expect(within(report).getByTestId('job-report-partial').textContent).toBe('Partial');
    expect(within(report).getByTestId('job-report-cut').textContent).toBe('Stopped early: spending limit reached');
    expect(report.textContent).not.toContain('notes I had time for');
    const card = within(report).getByTestId('artifact-card');
    expect(card.textContent).toContain('notes.md');
    expect(within(card).getByRole('button', { name: 'Download artifact' })).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Preview artifact' })).toBeTruthy();
  });

  it('a job that couldn’t finish says so in red, says why, and its name opens its Activity', async () => {
    ended('job-1', { at: 1_000_000, status: 'failed', errorCode: 'worker_lost' });
    view(h(TurnJobs, { jobs: [job('job-1', { status: 'failed', errorType: 'worker_lost' })] }));
    const report = await screen.findByTestId('job-report');
    const outcome = within(report).getByTestId('job-report-outcome');
    expect(outcome.textContent).toBe(" · couldn't finish");
    expect(outcome.getAttribute('style')).toContain('var(--red-11)');
    expect(within(report).getByTestId('job-report-failure').textContent?.length).toBeGreaterThan(0);
    fireEvent.click(within(report).getByRole('button', { name: 'Job job-1' }));
    expect(useActivityPanelStore.getState().jobSection).toBe('activity');
  });

  it('a job you stopped says you stopped it', async () => {
    ended('job-1', { at: 1_000_000, status: 'cancelled' });
    view(h(TurnJobs, { jobs: [job('job-1', { status: 'failed', errorType: 'cancelled' })] }));
    const report = await screen.findByTestId('job-report');
    expect(within(report).getByTestId('job-report-outcome').textContent).toBe(' · was stopped by you');
  });
});

describe('BGUI-6: several jobs, in finish order', () => {
  it('report in the order they finished, leave their replies to PipesHub, and a plain done with nothing new has none', async () => {
    ended('job-a', { at: 3_000_000, finished: 'A is done.', artifact: { name: 'a.csv', id: 'rec-a' } });
    ended('job-b', { at: 1_000_000, finished: 'B is done.', artifact: { name: 'b.csv', id: 'rec-b' } });
    ended('job-c', { at: 2_000_000, finished: 'C is done.' });
    ended('job-d', { at: 1_500_000, status: 'failed', errorCode: 'worker_lost' });
    view(h(TurnJobs, { jobs: [job('job-a'), job('job-b'), job('job-c'), job('job-d', { status: 'failed', errorType: 'worker_lost' })] }));
    await waitFor(() => expect(reports()).toHaveLength(3));
    expect(reports().map((r) => r.getAttribute('data-run-id'))).toEqual(['job-b', 'job-d', 'job-a']);
    expect(screen.queryByText(/is done\./)).toBeNull();
    expect(screen.getAllByTestId('job-card')).toHaveLength(4);
  });

  it('a file the answer’s own file cards show is left to them', async () => {
    ended('job-a', { at: 2_000_000, artifact: { name: 'a.csv', id: 'rec-a' } });
    ended('job-b', { at: 1_000_000, artifact: { name: 'b.csv', id: 'rec-b' } });
    view(h(TurnJobs, { jobs: [job('job-a'), job('job-b')], shownFiles: new Set(['rec-a']) }));
    await waitFor(() => expect(reports()).toHaveLength(1));
    expect(reports()[0].getAttribute('data-run-id')).toBe('job-b');
  });

  it('after a reload, whose streams are not replayed, one read of the request puts them in finish order', async () => {
    const digest = (name: string, recordId: string) => ({ outcome: 'answered', artifacts: [{ name, recordId }], files: { added: 1, modified: 0, deleted: 0, working: 0 }, durationMs: 4_000 });
    const base = { parentRunId: 'run-root', status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null };
    timelines['job-a'] = { ...base, runId: 'job-a', digest: digest('a.csv', 'rec-a') };
    timelines['job-b'] = { ...base, runId: 'job-b', digest: digest('b.csv', 'rec-b') };
    requestJobs = {
      request: { runId: 'run-root', sessionId: SESSION, state: 'finished', status: 'completed', ask: 'x' },
      jobs: [
        { runId: 'job-a', title: 'a', state: 'finished', status: 'completed', engine: 'claude_code', createdAt: 1, endedAt: 900, continuesRunId: null },
        { runId: 'job-b', title: 'b', state: 'finished', status: 'completed', engine: 'claude_code', createdAt: 1, endedAt: 500, continuesRunId: null },
      ],
      total: 2,
      back: 2,
    };
    view(h(TurnJobs, { jobs: [job('job-a'), job('job-b')] }));
    await waitFor(() => expect(reports().map((r) => r.getAttribute('data-run-id'))).toEqual(['job-b', 'job-a']));
  });

  it('its working files, the server’s count, fold into one line that opens the job’s Outputs', async () => {
    useJobsStore.setState({
      streams: {
        'job-a': { ...emptyJobStream('job-a'), status: 'completed', ended: true, finishedAt: 2, startedAt: 1, frames: [frame('job-a', 'diff', 'src/a.ts'), frame('job-a', 'file', 'src/b.ts')] },
      },
    });
    const base = { parentRunId: 'run-root', status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null };
    timelines['job-a'] = { ...base, runId: 'job-a', digest: { outcome: 'answered', artifacts: [], files: { added: 0, modified: 3, deleted: 0, working: 3 }, durationMs: 1 } };
    view(h(TurnJobs, { jobs: [job('job-a'), job('job-b', { status: 'running' })] }));
    const report = await screen.findByTestId('job-report');
    // The summary's count, not the two diff lines its stream happened to hold: every surface reads one number.
    fireEvent.click(await within(report).findByRole('button', { name: '3 working files' }));
    expect(useActivityPanelStore.getState().jobSection).toBe('outputs');
  });
});

describe('BGUI-6: the order rule', () => {
  it('earliest end first, unknown ends last in hand-out order', () => {
    expect(inFinishOrder([{ id: 1 }, { id: 2, finishedAt: 5 }, { id: 3 }, { id: 4, finishedAt: 2 }]).map((r) => r.id)).toEqual([4, 2, 1, 3]);
  });

  it('a running job has no facts; a stop for budget is partial', () => {
    expect(jobReportFacts({ part: job('j', { status: 'running' }), isOwner: true })).toBeNull();
    const facts = jobReportFacts({ part: job('j', { status: 'failed', errorType: 'budget_exhausted' }), isOwner: true });
    expect(facts?.state.status).toBe('stopped');
    expect(facts?.partial).toBe('budget');
  });
});
