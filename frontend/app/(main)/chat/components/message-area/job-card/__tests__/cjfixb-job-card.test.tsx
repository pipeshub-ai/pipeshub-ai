/**
 * CJFIX-B (ADR-CJ-01) on the job card: a Stop's 409 outlives the card that sent it when the answer settles (F9), the
 * status never goes back a step (F6), a loop sub-agent is named by its goal (F10), and a shared viewer's card reads
 * no timeline of the job (F3).
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { MessagePart } from '../../../../types';

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
const { SubAgentGroup } = await import('../../agent-activity');
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

describe('F9: the Stop outcome is the job’s', () => {
  it('"It had already finished" stays when the settled answer draws the card again', async () => {
    apiClient.post.mockRejectedValue({ statusCode: 409 });
    streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }];
    const first = view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(card().getAttribute('data-status')).toBe('working'));
    fireEvent.click(screen.getByTestId('job-card-stop'));
    await waitFor(() => expect(screen.getByTestId('job-card-stop-gone').textContent).toBe('It had already finished'));
    first.unmount();
    // The saved answer's copy of the card: a new component, the job's part now ended.
    view(h(SubAgentGroup, { part: job('job-1', { status: 'completed' }) }));
    expect(card().getAttribute('data-status')).toBe('done');
    expect(screen.getByTestId('job-card-stop-gone').textContent).toBe('It had already finished');
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });
});

describe('F6: a status never goes back a step', () => {
  it('handed out it reads Starting until its own stream says it started, then Working', async () => {
    streams.frames['job-b'] = [{ type: 'STATE_SNAPSHOT', snapshot: { run_id: 'job-b', status: 'queued' } }];
    streams.frames['job-c'] = [{ type: 'STATE_SNAPSHOT', snapshot: { run_id: 'job-c', status: 'queued' } }, { type: 'RUN_STARTED', runId: 'job-c' }];
    const shown = (runId: string) => document.querySelector(`[data-run-id="${runId}"]`)?.getAttribute('data-status');
    view(h(React.Fragment, null, h(SubAgentGroup, { part: job('job-a') }), h(SubAgentGroup, { part: job('job-b') }), h(SubAgentGroup, { part: job('job-c') })));
    await waitFor(() => expect(shown('job-c')).toBe('working'));
    // Nothing heard yet, and queued: both Starting, so a later Working is a step forward, never back.
    expect(shown('job-a')).toBe('upNext');
    expect(shown('job-b')).toBe('upNext');
  });

  it('a job whose part already has steps reads Working, whatever a late queued snapshot says', async () => {
    streams.frames['job-2'] = [{ type: 'STATE_SNAPSHOT', snapshot: { run_id: 'job-2', status: 'queued' } }];
    view(h(SubAgentGroup, { part: job('job-2', { parts: [{ type: 'tool_call', toolCallId: 't1', toolName: 'search', status: 'completed' }] }) }));
    await waitFor(() => expect(streams.opened).toContain('job-2'));
    expect(card().getAttribute('data-status')).toBe('working');
  });
});

describe('F10: a loop sub-agent is named by its goal', () => {
  it('its card, and its Stop, use the goal and never the role id', async () => {
    streams.frames['loop-1'] = [{ type: 'RUN_STARTED', runId: 'loop-1' }];
    const part = job('loop-1', { codeTaskGoal: undefined, roleName: 'pipeshub-subagent-general', goal: 'Read the Q3 tickets and release them' });
    view(h(SubAgentGroup, { part }));
    await waitFor(() => expect(card().getAttribute('data-status')).toBe('working'));
    expect(card().getAttribute('aria-label')).toBe('Read the Q3 tickets and release them, Working');
    expect(screen.getByTestId('job-card-stop').getAttribute('aria-label')).toBe('Stop job: Read the Q3 tickets and release them');
    expect(card().textContent).not.toContain('pipeshub-subagent');
  });
});

describe('F3: a shared viewer reads no timeline of the job', () => {
  it('its card follows the stream to the end and asks for nothing else', async () => {
    const slotId = useChatStore.getState().activeSlotId as string;
    useChatStore.getState().updateSlot(slotId, { isOwner: false });
    streams.frames['job-1'] = [{ type: 'RUN_STARTED', runId: 'job-1' }, { type: 'RUN_FINISHED', runId: 'job-1' }];
    view(h(SubAgentGroup, { part: job('job-1') }));
    await waitFor(() => expect(card().getAttribute('data-status')).toBe('done'));
    await act(async () => undefined);
    expect(apiClient.get.mock.calls.filter(([url]) => String(url).includes('/timeline'))).toEqual([]);
  });
});
