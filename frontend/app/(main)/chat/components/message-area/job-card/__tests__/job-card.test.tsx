/**
 * CJ-2 (CLASSIC-JOBS D2, D3, D11, D13, D14, §7): the job card in the chat. Live from the job's own stream (at most
 * four at once, no timeline poll), Stop on the collapsed card (one click; a 409 reads "It had already finished"),
 * Details opens the job's panel, the job's asks are answered on the card, a shared viewer sees status only, and a
 * skill the engine read is its own step.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
const { AgentActivityTimeline, SubAgentGroup } = await import('../../agent-activity');
const { useJobsStore } = await import('../data');
const { useActivityPanelStore } = await import('../../../activity-panel/activity-panel-store');
const { requestAskFocus } = await import('../../../../hooks/use-interaction-focus');

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

const OWNER_PDF_COMMAND =
  "/bin/bash -lc \"cat /pipeshub/AGENTS.md && printf '\\n---PDF SKILL---\\n' && cat /skills/pdf/SKILL.md && printf '\\n---TOOLS INDEX---\\n' && cat /pipeshub/tools/INDEX.md\"";

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
const card = () => screen.getByTestId('job-card');

describe('the card follows the job’s own stream (D13)', () => {
  it('Working with what it does now and how long it has run, live from the stream', async () => {
    streams.frames['job-1'] = [
      { type: 'RUN_STARTED', runId: 'job-1', timestamp: Date.now() - 134_000 },
      progress('job-1', 'command', 'npm test', { callId: 'c1' }),
    ];
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('job-card-now').textContent).toBe('Running npm test'));
    expect(card().getAttribute('data-status')).toBe('working');
    expect(screen.getByTestId('job-card-status').textContent).toBe('Working');
    expect(screen.getByTestId('job-card-elapsed').textContent).toMatch(/^2m 1[45]s$/);
    expect(screen.getByTestId('sub-agent-toggle').textContent).toContain('1 step');
    expect(card().getAttribute('aria-label')).toBe('Build the quarterly PDF report, Working');
  });

  it('never reads the timeline on a timer while the job runs', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }];
      view(h(SubAgentGroup, { part: job('job-1') }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(apiClient.get.mock.calls.filter(([url]) => String(url).endsWith('/timeline'))).toHaveLength(0);
      expect(streams.opened).toEqual(['job-1']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('follows at most four jobs at once; a fifth waits its turn', async () => {
    const parts = ['a', 'b', 'c', 'd', 'e'].map((id) => job(`job-${id}`));
    const { unmount } = view(h(AgentActivityTimeline, { parts, isNested: true }));
    await waitFor(() => expect(streams.opened).toHaveLength(4));
    expect(streams.maxOpen).toBe(4);
    unmount();
    await waitFor(() => expect(streams.open).toBe(0));
  });

  it('a job that had already ended is not replayed', () => {
    view(h(SubAgentGroup, { part: job('job-1', { status: 'completed' }) }));
    expect(streamSSEGet).not.toHaveBeenCalled();
    expect(screen.getByTestId('job-card-status').textContent).toBe('Done');
    expect(screen.getByTestId('job-card-now').textContent).toBe('Finished');
  });

  it('the stream’s end turns the card to how it ended, without a reload', async () => {
    streams.frames['job-1'] = [
      { type: 'RUN_STARTED', runId: 'job-1' },
      progress('job-1', 'artifact', 'report-q3.pdf', { artifactId: 'a1' }),
      { type: 'RUN_FINISHED', runId: 'job-1' },
    ];
    apiClient.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/runs/job-1/timeline')) return { data: { runId: 'job-1', status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null } };
      throw new Error(`unexpected GET ${url}`);
    });
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(card().getAttribute('data-status')).toBe('done'));
    expect(screen.getByTestId('job-card-now').textContent).toBe('Made report-q3.pdf');
    expect(screen.queryByTestId('job-card-stop')).toBeNull();
  });
});

describe('Stop and Details on the collapsed card (D11, D4)', () => {
  it('Stop is one click with no confirm, and a 409 reads "It had already finished"', async () => {
    apiClient.post.mockRejectedValue({ statusCode: 409 });
    view(h(SubAgentGroup, { part: job('job-1') }));
    const stop = screen.getByRole('button', { name: 'Stop job: Build the quarterly PDF report' });
    await act(async () => {
      fireEvent.click(stop);
    });
    expect(apiClient.post).toHaveBeenCalledWith(
      `/api/v1/conversations/${SESSION}/runs/job-1/cancel`,
      { idempotencyKey: 'stop-job-1' },
      expect.anything(),
    );
    expect(screen.getByTestId('job-card-stop-gone').textContent).toBe('It had already finished');
    expect(screen.queryByTestId('job-card-stop')).toBeNull();
  });

  it('a Stop that could not be sent says so and can be tried again', async () => {
    apiClient.post.mockRejectedValue(new Error('network'));
    view(h(SubAgentGroup, { part: job('job-1') }));
    await act(async () => {
      fireEvent.click(screen.getByTestId('job-card-stop'));
    });
    expect(screen.getByText("Couldn't stop it. Try again.")).toBeTruthy();
    expect((screen.getByTestId('job-card-stop') as HTMLButtonElement).disabled).toBe(false);
  });

  it('a stop the stream reports reads "Stopped by you"', async () => {
    streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }, { type: 'RUN_ERROR', runId: 'job-1', code: 'cancelled' }];
    apiClient.get.mockResolvedValue({ data: { runId: 'job-1', status: 'cancelled', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null } });
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('job-card-status').textContent).toBe('Stopped by you'));
  });

  it('an admin’s stop is read once from the ended job’s timeline', async () => {
    apiClient.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/runs/job-1/timeline'))
        return { data: { runId: 'job-1', status: 'cancelled', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null, operatorStop: { scope: 'org', reason: 'security', at: 1 } } };
      throw new Error(`unexpected GET ${url}`);
    });
    view(h(SubAgentGroup, { part: job('job-1', { status: 'failed', errorType: 'cancelled' }) }));
    await waitFor(() => expect(screen.getByTestId('job-card-status').textContent).toBe('Stopped by your admin: security'));
    expect(apiClient.get.mock.calls.filter(([url]) => String(url).endsWith('/timeline'))).toHaveLength(1);
  });

  it('the card itself opens the job’s own run in the panel, named by its visible words (BGUI-2)', () => {
    view(h(SubAgentGroup, { part: job('job-1') }));
    const open = screen.getByRole('button', { name: /^Build the quarterly PDF report Starting/ });
    expect(open).toBe(screen.getByTestId('job-card-details'));
    expect(open.id).toBe('chat-job-details-job-1');
    expect(open.getAttribute('aria-label')).toBeNull();
    fireEvent.click(open);
    expect(useActivityPanelStore.getState()).toMatchObject({ open: true, run: { sessionId: SESSION, runId: 'job-1' } });
    expect(open.getAttribute('aria-pressed')).toBe('true');
  });

  it('a shared viewer sees the status and the now line, never Stop or Details (D14)', async () => {
    const slotId = useChatStore.getState().activeSlotId as string;
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }, progress('job-1', 'command', 'make', { callId: 'c1' })];
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('job-card-now').textContent).toBe('Running make'));
    expect(screen.queryByTestId('job-card-stop')).toBeNull();
    expect(screen.queryByTestId('job-card-details')).toBeNull();
    fireEvent.click(screen.getByTestId('job-card-header'));
    expect(useActivityPanelStore.getState().open).toBe(false);
    // Its steps open from the stream alone: the timeline is the owner's route.
    fireEvent.click(screen.getByTestId('sub-agent-toggle'));
    const panel = await screen.findByTestId('child-run-panel');
    expect(within(panel).getByTestId('engine-command').textContent).toContain('make');
    expect(apiClient.get).not.toHaveBeenCalled();
  });
});

describe('the job’s asks are answered on the card (D3)', () => {
  it('a Needs you link naming the job’s ask opens its steps, where the ask is drawn', async () => {
    streams.frames['job-1'] = [
      { type: 'RUN_STARTED', runId: 'job-1' },
      { type: 'CUSTOM', name: 'approval_requested', runId: 'job-1', value: { interactionId: 'ix-9', toolName: 'mail__send_email' } },
    ];
    apiClient.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
      if (url.endsWith('/runs/job-1/timeline')) return { data: { runId: 'job-1', status: 'suspended', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null } };
      throw new Error(`unexpected GET ${url}`);
    });
    act(() => requestAskFocus('ix-9'));
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('sub-agent-toggle').getAttribute('aria-expanded')).toBe('true'));
    act(() => requestAskFocus(null));
  });

  it('the chat’s card for the job’s ask is drawn inside the job card, which reads Needs you', () => {
    const anchored = new Map([['run:job-1', h('div', { 'data-testid': 'the-ask' }, 'Allow sending an email?')]]);
    view(h(AgentActivityTimeline, { parts: [job('job-1')], isNested: true, anchored, waiting: new Set(['run:job-1']) }));
    expect(within(card()).getByTestId('the-ask')).toBeTruthy();
    expect(screen.getAllByTestId('the-ask')).toHaveLength(1);
    expect(card().getAttribute('data-status')).toBe('needsYou');
    expect(screen.getByTestId('job-card-status').textContent).toBe('Needs you');
    expect(screen.getByRole('alert').textContent).toBe('Build the quarterly PDF report needs you');
  });
});

describe('the card renders FIX-SKILLSHOW’s labelled skill read (§7)', () => {
  it('the owner’s chained command, still running, reads "Reading the PDF skill" on the card and in its steps; the command stays behind it', async () => {
    streams.frames['job-1'] = [
      { type: 'RUN_STARTED', runId: 'job-1' },
      progress('job-1', 'started', 'Build the report'),
      progress('job-1', 'command', OWNER_PDF_COMMAND, { callId: 'c1', skills: ['pdf'] }),
    ];
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('job-card-now').textContent).toBe('Reading the PDF skill'));
    fireEvent.click(screen.getByTestId('sub-agent-toggle'));
    const panel = await screen.findByTestId('child-run-panel');
    const command = within(panel).getByTestId('engine-command');
    expect(within(command).getByTestId('engine-command-skill').textContent).toBe('Reading the PDF skill');
    expect(command.getAttribute('data-skill')).toBe('pdf');
    fireEvent.click(within(command).getByRole('button'));
    expect(within(command).getByTestId('engine-command-line').textContent).toContain('cat /skills/pdf/SKILL.md');
    expect(panel.textContent).not.toMatch(/(AGENTS|INDEX) skill/i);
  });

  it('a command that read no skill is left as it ran', async () => {
    streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }, progress('job-1', 'command', 'cat /skills/pdf/forms.md', { callId: 'c2' })];
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(screen.getByTestId('job-card-now').textContent).toBe('Running cat /skills/pdf/forms.md'));
  });
});
