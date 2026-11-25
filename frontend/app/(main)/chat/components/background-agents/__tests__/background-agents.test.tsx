/**
 * BGUI-5: the chat's Background agents list and its top-bar button. The jobs are the chat's own `sub_agent` parts
 * (the cards' source), live from the shared job streams; the list groups a request's jobs, folds Finished and each
 * agent's tasks, opens a job through the chat's one way in (`openJob`), and never lists another chat's jobs.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { i18n } from '@/lib/i18n';
import type { MessagePart } from '../../../types';

type StreamOptions = { signal: AbortSignal; onEvent: (event: unknown) => void };

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const streams = vi.hoisted(() => ({ frames: {} as Record<string, unknown[]>, opened: [] as string[], emit: {} as Record<string, (f: unknown) => void> }));
const streamSSEGet = vi.hoisted(() =>
  vi.fn(async (url: string, options: StreamOptions) => {
    const runId = /\/runs\/([^/]+)\/stream$/.exec(url)?.[1] ?? '';
    streams.opened.push(runId);
    streams.emit[runId] = (frame) => options.onEvent({ event: 'message', data: frame });
    for (const frame of streams.frames[runId] ?? []) options.onEvent({ event: 'message', data: frame });
    await new Promise<void>((resolve) => {
      if (options.signal.aborted) resolve();
      options.signal.addEventListener('abort', () => resolve());
    });
  }),
);
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet }));
const viewport = vi.hoisted(() => ({ isMobile: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => viewport.isMobile, useIsBelowDesktop: () => viewport.isMobile }));

const { useChatStore } = await import('../../../store');
const { useJobsStore } = await import('@/lib/harness-jobs/jobs-store');
const { useActivityPanelStore } = await import('../../activity-panel/activity-panel-store');
const { BackgroundAgentsButton, BackgroundAgentsHost, useBackgroundAgentsStore } = await import('..');
const { chatJobRefs, openerState, tasksOf } = await import('../chat-jobs');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const job = (runId: string, patch: Partial<MessagePart> = {}): MessagePart => ({
  type: 'sub_agent',
  runId,
  parentRunId: 'run-root',
  codeTaskGoal: `Goal of ${runId}`,
  codeTaskHandoff: { engine: 'claude-code' } as MessagePart['codeTaskHandoff'],
  status: 'running',
  parts: [],
  ...patch,
});

const progress = (runId: string, kind: string, text = '', data: Record<string, unknown> = {}) => ({
  type: 'CUSTOM',
  name: 'engine_progress',
  runId,
  value: { tool: 'code_task', taskId: runId, kind, text, data },
});

const user = (text: string): ThreadMessageLike => ({ role: 'user', content: [{ type: 'text', text }] });
const answer = (parts: MessagePart[]): ThreadMessageLike => ({ role: 'assistant', content: [{ type: 'text', text: '' }], metadata: { custom: { persistedParts: parts } } });

function chat(messages: ThreadMessageLike[], patch: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().activeSlotId as string;
  useChatStore.getState().updateSlot(slotId, { messages, isOwner: true, ...patch });
}

const wrap = (node: React.ReactNode) => render(h(Theme, null, node));

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {}, cleared: [] });
  useActivityPanelStore.setState({ open: false, sheetOpen: false, run: null, focusSection: null, jobSection: null });
  useBackgroundAgentsStore.setState({ openFor: null, request: null });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  streams.frames = {};
  streams.opened = [];
  streams.emit = {};
  viewport.isMobile = false;
  apiClient.get.mockImplementation(async (url: string) => {
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('chat jobs model', () => {
  it('reads every job of the chat once, with what was asked in its turn', () => {
    const refs = chatJobRefs(
      [user('Fix the invoices'), answer([job('a'), job('b')]), user('Now the report'), answer([job('c', { parentRunId: 'run-2' }), job('a')])],
      [job('d', { parentRunId: 'run-3' })],
    );
    expect(refs.map((r) => [r.runId, r.ask])).toEqual([
      ['a', 'Fix the invoices'],
      ['b', 'Fix the invoices'],
      ['c', 'Now the report'],
      ['d', 'Now the report'],
    ]);
  });

  it('the opener counts what still goes and what needs you', () => {
    expect(openerState([])).toEqual({ shown: false, live: 0, needsYou: 0 });
    expect(openerState([{ status: 'working' }, { status: 'needsYou' }, { status: 'done' }])).toEqual({ shown: true, live: 2, needsYou: 1 });
  });

  it('ticks off the tasks it did and spins on the one in hand', () => {
    const steps = ['s1', 's2', 's3'].map((id) => ({ id, kind: 'command' as const, title: `Ran ${id}`, activeTitle: `Running ${id}`, ms: 0 }));
    const state = { done: 2, startedAt: 1, notes: [], answers: {}, skipped: [] };
    const worker = { steps } as never;
    expect(tasksOf({ status: 'working', worker, state }).map((t) => [t.title, t.mark])).toEqual([
      ['Ran s1', 'done'],
      ['Ran s2', 'done'],
      ['Running s3', 'now'],
    ]);
    expect(tasksOf({ status: 'done', worker, state: { ...state, done: 3 } }).every((t) => t.mark === 'done')).toBe(true);
  });
});

describe('Background agents button', () => {
  it('is not there until the chat has a background job', () => {
    chat([user('Hi'), answer([])]);
    wrap(h(BackgroundAgentsButton, { isMobile: false }));
    expect(screen.queryByTestId('background-agents-button')).toBeNull();
  });

  it('shows how many still run, and its name says the same number', () => {
    chat([user('Fix it'), answer([job('a'), job('b'), job('c', { status: 'completed' })])]);
    wrap(h(BackgroundAgentsButton, { isMobile: false }));
    const button = screen.getByTestId('background-agents-button');
    expect(screen.getByTestId('background-agents-count').textContent).toBe('2');
    expect(button.getAttribute('aria-label')).toBe('Background agents, 2 running');
    // The icon is aria-hidden: the visible text is the count, and the name holds it.
    expect(button.getAttribute('aria-label')).toContain(screen.getByTestId('background-agents-count').textContent ?? '');
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('turns amber with the count that needs you', () => {
    chat([user('Fix it'), answer([job('a'), job('b')])], {
      pendingApprovals: [{ interactionId: 'ix-1', childRunId: 'a', toolName: 'mail__send', argsSummary: null, status: 'pending' }],
    });
    wrap(h(BackgroundAgentsButton, { isMobile: false }));
    const count = screen.getByTestId('background-agents-count');
    expect(count.dataset.tone).toBe('needsYou');
    expect(count.textContent).toBe('1');
    expect(screen.getByTestId('background-agents-button').getAttribute('aria-label')).toBe('Background agents, 1 needs you');
  });

  it('carries no count once every job ended, and opens the list in place of the job panel', () => {
    chat([user('Fix it'), answer([job('a', { status: 'completed' })])]);
    useActivityPanelStore.getState().showJob({ sessionId: SESSION }, 'a');
    wrap(h(BackgroundAgentsButton, { isMobile: false }));
    expect(screen.queryByTestId('background-agents-count')).toBeNull();
    const button = screen.getByTestId('background-agents-button');
    expect(button.getAttribute('aria-label')).toBe('Background agents');
    fireEvent.click(button);
    expect(useBackgroundAgentsStore.getState().openFor).toBe(SESSION);
    expect(useActivityPanelStore.getState().run).toBeNull();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(button);
    expect(useBackgroundAgentsStore.getState().openFor).toBeNull();
  });
});

describe('Background agents list', () => {
  const open = () => useBackgroundAgentsStore.getState().open(SESSION);

  it('puts each job in the section for where it stands, a request with several on one card under what was asked', async () => {
    chat([
      user('Fix PAY-1423'),
      answer([job('a'), job('b')]),
      user('Write the summary'),
      answer([job('c', { parentRunId: 'run-2', status: 'completed' })]),
    ]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    const working = screen.getByTestId('background-agents-section-working');
    expect(within(working).getByRole('heading', { name: 'Working 2' })).toBeTruthy();
    expect(within(working).getByTestId('background-agents-request').textContent).toContain('Fix PAY-1423');
    expect(within(working).getByTestId('background-agents-request').textContent).toContain('2 jobs');
    expect(within(working).getAllByTestId('background-agents-row')).toHaveLength(2);
    // Finished folds while anything still goes.
    const finished = screen.getByTestId('background-agents-section-finished');
    expect(within(finished).queryByTestId('background-agents-row')).toBeNull();
    fireEvent.click(within(finished).getByRole('button', { name: /Finished/ }));
    expect(within(finished).getAllByTestId('background-agents-row')).toHaveLength(1);
  });

  it('lists an agent\'s tasks live from its stream, and the stream is the one the cards share', async () => {
    streams.frames.a = [{ type: 'RUN_STARTED', runId: 'a' }, progress('a', 'command', 'make', { callId: 'c1' })];
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    const fold = await screen.findByTestId('background-agents-tasks');
    expect(fold.textContent).toMatch(/(^|_down)1 task$/);
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    await act(async () => {
      streams.emit.a(progress('a', 'command', 'npm test', { callId: 'c2' }));
    });
    await waitFor(() => expect(screen.getByTestId('background-agents-tasks').textContent).toMatch(/(^|_down)2 tasks$/));
    expect(streams.opened).toEqual(['a']);
  });

  it('opens a job through the chat\'s job panel and closes itself', () => {
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Goal of a' }));
    expect(useActivityPanelStore.getState().run?.runId).toBe('a');
    expect(useActivityPanelStore.getState().run?.job).toBe(true);
    expect(useBackgroundAgentsStore.getState().openFor).toBeNull();
  });

  it('closes when the job panel opens from a card', () => {
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    act(() => useActivityPanelStore.getState().showJob({ sessionId: SESSION }, 'a'));
    expect(useBackgroundAgentsStore.getState().openFor).toBeNull();
  });

  it('stops a running job with one click, owner only', async () => {
    apiClient.post.mockResolvedValue({ data: { cancelled: true } });
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop Goal of a' }));
    await waitFor(() => expect(useJobsStore.getState().stops.a).toBeDefined());
    cleanup();
    chat([user('Fix it'), answer([job('a')])], { isOwner: false });
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    expect(screen.queryByRole('button', { name: 'Stop Goal of a' })).toBeNull();
  });

  it('keeps the focus in the list once a job stopped from it ends, so Escape still closes the list', async () => {
    apiClient.post.mockResolvedValue({ data: { cancelled: true } });
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    const stop = screen.getByRole('button', { name: 'Stop Goal of a' });
    stop.focus();
    fireEvent.click(stop);
    await waitFor(() => expect(useJobsStore.getState().stops.a).toBeDefined());
    act(() => chat([user('Fix it'), answer([job('a', { status: 'cancelled' })])]));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop Goal of a' })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('background-agents-panel')));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useBackgroundAgentsStore.getState().openFor).toBeNull();
  });

  it('clears the finished ones without a confirm, and says Nothing here when that was all', () => {
    chat([user('Fix it'), answer([job('a', { status: 'completed' })])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear finished agents' }));
    expect(useJobsStore.getState().cleared).toEqual(['a']);
    expect(screen.getByTestId('background-agents-empty').textContent).toContain('Nothing here');
  });

  it('opens a request\'s view: what was asked and how many are back', async () => {
    apiClient.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/runs/run-root/jobs'))
        return {
          data: {
            request: { runId: 'run-root', sessionId: SESSION, ask: 'Fix PAY-1423' },
            total: 2,
            back: 1,
            jobs: [
              { runId: 'a', title: 'Goal of a', engine: 'claude-code', state: 'working', status: 'running', createdAt: 1 },
              { runId: 'b', title: 'Goal of b', engine: 'codex', state: 'finished', status: 'completed', createdAt: 2, endedAt: 5 },
            ],
          },
        };
      throw new Error(`unexpected GET ${url}`);
    });
    chat([user('Fix PAY-1423'), answer([job('a'), job('b')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    fireEvent.click(screen.getByTestId('background-agents-request'));
    const view = await screen.findByTestId('background-agents-request-view');
    expect(view.textContent).toContain('Fix PAY-1423');
    expect(view.textContent).toContain('Ran 2 jobs');
    expect(screen.getByTestId('background-agents-request-progress').textContent).toBe('1 of 2 back');
    fireEvent.click(screen.getByRole('button', { name: 'Back to background agents' }));
    expect(screen.getByTestId('background-agents-list')).toBeTruthy();
  });

  it('only lists this chat\'s jobs', () => {
    chat([user('Fix it'), answer([job('a')])]);
    const other = useChatStore.getState().createSlot('ses-2');
    useChatStore.getState().updateSlot(other, { messages: [user('Elsewhere'), answer([job('z')])] });
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: false }));
    expect(screen.getAllByTestId('background-agents-row').map((r) => r.dataset.runId)).toEqual(['a']);
  });

  it('on a phone it is a sheet with 44px rows', () => {
    viewport.isMobile = true;
    chat([user('Fix it'), answer([job('a')])]);
    open();
    wrap(h(BackgroundAgentsHost, { isMobile: true }));
    const panel = screen.getByTestId('background-agents-panel');
    const row = within(panel).getByRole('button', { name: 'Goal of a' });
    expect(row.style.minHeight).toBe('44px');
    expect(within(panel).getByRole('button', { name: 'Stop Goal of a' }).style.minHeight).toBe('44px');
  });
});
