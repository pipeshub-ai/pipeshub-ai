/**
 * BGUI-2: the turn's jobs, drawn once and after the turn's steps (design `afterRun`, §6B.42). The root steps leave a
 * job out when the chat lifts it; up to three jobs are a card each, past three one board that folds; a hand-off the
 * run made on its own is one plain line before them; the card wears its engine's logo and reads "Working · 2m 14s ·
 * Claude Code"; a job's ask stays on its card, on a board row too.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { MessagePart, SSEEngineProgressEvent } from '../../../../types';

type StreamOptions = { signal: AbortSignal; onEvent: (event: unknown) => void; onError?: (e: Error) => void };

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const streams = vi.hoisted(() => ({ frames: {} as Record<string, unknown[]>, opened: [] as string[], open: 0, maxOpen: 0 }));
const streamSSEGet = vi.hoisted(() =>
  vi.fn(async (url: string, options: StreamOptions) => {
    const runId = /\/runs\/([^/]+)\/stream$/.exec(url)?.[1] ?? '';
    streams.opened.push(runId);
    streams.open += 1;
    streams.maxOpen = Math.max(streams.maxOpen, streams.open);
    const frames = streams.frames[runId] ?? [];
    for (const frame of frames) options.onEvent({ event: 'message', data: frame });
    const last = frames[frames.length - 1] as { type?: string } | undefined;
    // The server closes an ended job's stream; a live one stays open until the card lets go of it.
    if (last?.type !== 'RUN_FINISHED' && last?.type !== 'RUN_ERROR')
      await new Promise<void>((resolve) => {
        if (options.signal.aborted) resolve();
        options.signal.addEventListener('abort', () => resolve());
      });
    streams.open -= 1;
  }),
);
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet }));

const { useChatStore } = await import('../../../../store');
const { AgentActivityTimeline } = await import('../../agent-activity');
const { TurnJobs } = await import('../turn-jobs');
const { useJobsStore } = await import('../data');
const { useActivityPanelStore } = await import('../../../activity-panel/activity-panel-store');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const job = (runId: string, patch: Partial<MessagePart> = {}): MessagePart => ({
  type: 'sub_agent',
  runId,
  parentRunId: 'run-root',
  codeTaskGoal: 'Build the quarterly PDF report',
  codeTaskHandoff: undefined,
  status: 'running',
  parts: [],
  ...patch,
});

const progress = (runId: string, kind: string, text = '', data: Record<string, unknown> = {}) => ({
  type: 'CUSTOM',
  name: 'engine_progress',
  runId,
  value: { tool: 'code_task', taskId: runId, kind, text, data } satisfies SSEEngineProgressEvent,
});

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, run: null, focusSection: null });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  streams.frames = {};
  streams.opened = [];
  streams.open = 0;
  streams.maxOpen = 0;
  apiClient.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (node: React.ReactElement) => render(h(Theme, null, node));
const parentText: MessagePart = { type: 'text', content: 'I handed it to a background agent.', settled: true };

describe('BGUI-2: one drawing of each job', () => {
  it('the steps leave a lifted job out, and the jobs after them draw it once', () => {
    const parts = [parentText, job('job-1')];
    view(h(React.Fragment, null, h(AgentActivityTimeline, { parts, liftJobs: true }), h(TurnJobs, { jobs: [job('job-1')] })));
    expect(screen.getAllByTestId('job-card')).toHaveLength(1);
    expect(within(screen.getByTestId('turn-jobs')).getByTestId('job-card')).toBeTruthy();
    expect(within(screen.getByTestId('agent-activity-rows')).queryByTestId('job-card')).toBeNull();
    expect(screen.getByText('I handed it to a background agent.')).toBeTruthy();
  });

  it('a timeline that does not lift (a job’s own steps, nested) still draws its child job in place', () => {
    view(h(AgentActivityTimeline, { parts: [job('job-1')], isNested: true, liftJobs: true }));
    expect(screen.getAllByTestId('job-card')).toHaveLength(1);
  });

  it('the hand-off’s own code_task call goes with its job, not back into the steps', () => {
    const call: MessagePart = { type: 'tool_call', toolCallId: 'c1', toolName: 'code_task', status: 'completed' };
    view(h(AgentActivityTimeline, { parts: [parentText, call, job('job-1')], liftJobs: true }));
    expect(screen.queryByTestId('job-card')).toBeNull();
    expect(screen.queryByText(/code_task|Code task/i)).toBeNull();
  });
});

describe('BGUI-2: cards up to three, one board past three', () => {
  const jobs = (n: number) => Array.from({ length: n }, (_, i) => job(`job-${i}`, { codeTaskGoal: `Job ${i}` }));

  it('three jobs are three cards; four are one board of four rows that folds', () => {
    const first = view(h(TurnJobs, { jobs: jobs(3) }));
    expect(screen.queryByTestId('jobs-board')).toBeNull();
    expect(screen.getAllByTestId('job-card').map((c) => c.getAttribute('data-variant'))).toEqual(['card', 'card', 'card']);
    first.unmount();

    view(h(TurnJobs, { jobs: jobs(4) }));
    const board = screen.getByTestId('jobs-board');
    expect(within(board).getByTestId('jobs-board-count').textContent).toBe('4 agents');
    expect(within(board).getAllByTestId('job-card').map((c) => c.getAttribute('data-variant'))).toEqual(['row', 'row', 'row', 'row']);
    const fold = screen.getByRole('button', { name: 'Hide agents' });
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(fold);
    expect(screen.queryAllByTestId('job-card')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Show agents' }).getAttribute('aria-expanded')).toBe('false');
  });

  it('a job’s ask is drawn on its own row of the board, once', () => {
    const anchored = new Map([['run:job-2', h('div', { 'data-testid': 'the-ask' }, 'Allow sending an email?')]]);
    view(h(TurnJobs, { jobs: jobs(4), anchored, waiting: new Set(['run:job-2']) }));
    const row = document.querySelector('[data-testid="job-card"][data-run-id="job-2"]') as HTMLElement;
    expect(within(row).getByTestId('the-ask')).toBeTruthy();
    expect(screen.getAllByTestId('the-ask')).toHaveLength(1);
    expect(row.getAttribute('data-status')).toBe('needsYou');
  });
});

describe('BGUI-2: the card’s words', () => {
  it('a hand-off says why in one line before the card, and the card is named by its goal', () => {
    const part = job('job-1', { codeTaskHandoff: { trigger: 'upfront', reason: 'research', engine: 'claude_code' } });
    view(h(TurnJobs, { jobs: [part] }));
    expect(screen.getByTestId('job-handoff-line').textContent).toBe('Handed to Claude Code: this needs research across many sources');
    expect(screen.getByTestId('job-card').getAttribute('aria-label')).toBe('Build the quarterly PDF report, Starting');
    expect(screen.getByTestId('agent-tile').getAttribute('data-agent')).toBe('Claude Code');
  });

  it('reads "Working · 2m 14s · Claude Code" with what it does now beside its steps fold', async () => {
    streams.frames['job-1'] = [
      { type: 'RUN_STARTED', runId: 'job-1', timestamp: Date.now() - 134_000 },
      progress('job-1', 'command', 'npm test', { callId: 'c1' }),
    ];
    const part = job('job-1', { codeTaskHandoff: { trigger: 'upfront', reason: 'research', engine: 'claude_code' } });
    view(h(TurnJobs, { jobs: [part] }));
    await waitFor(() => expect(screen.getByTestId('job-card-now').textContent).toBe('Running npm test'));
    const pill = screen.getByTestId('job-card-details');
    expect(pill.textContent).toMatch(/^Build the quarterly PDF reportWorking · 2m 1[45]s · Claude Code$/);
    expect(screen.getByTestId('sub-agent-toggle').textContent).toContain('1 step');
    expect(screen.getByRole('button', { name: 'Stop job: Build the quarterly PDF report' })).toBeTruthy();
  });

  it('only a failure spends a colour', async () => {
    view(h(TurnJobs, { jobs: [job('job-1', { status: 'failed', errorType: 'engine_crashed' })] }));
    const word = screen.getByTestId('job-card-status');
    expect(word.textContent).toBe("Couldn't finish");
    expect(word.style.color).toBe('var(--red-11)');
    expect(screen.queryByTestId('job-card-stop')).toBeNull();
  });
});
