/**
 * CJFIX-B on the job panel: an ended job opens on Brief and its jump row says so (F7); an engine job that only ran
 * commands says where they are instead of "Nothing has run yet" (F7); its steps come from its own stream and its
 * timeline is read once, shared with the card (F8, ADR-CJ-01); a loop sub-agent is named by its goal (F10); the
 * amber "Answer in chat" reads in the light look (F5).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { SSEGetOptions } from '@/lib/api/streaming';
import type { ActivityView, ChatSlot, MessagePart, RunTimelineView } from '@/chat/types';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  jobEvents: [] as Array<{ event: string; data: unknown; id?: string }>,
  activityEvents: [] as Array<{ event: string; data: unknown }>,
  urls: [] as string[],
}));
vi.mock('@/lib/api', () => ({
  apiClient: { get: (...args: unknown[]) => api.get(...args), post: (...args: unknown[]) => api.post(...args), put: vi.fn(), delete: vi.fn() },
  streamSSEGet: (url: string, options: SSEGetOptions) => {
    api.urls.push(url);
    const events = url.endsWith('/stream') ? api.jobEvents : url.endsWith('/activity') ? api.activityEvents : [];
    for (const event of events) options.onEvent(event as never);
    return new Promise<void>((resolve) => options.signal?.addEventListener('abort', () => resolve(), { once: true }));
  },
}));

import { ActivityPanel } from '../activity-panel';
import { useActivityPanelStore, type PanelRun } from '../activity-panel-store';
import { useAskFocusStore } from '@/chat/hooks/use-interaction-focus';
import { useChatStore } from '@/chat/store';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';

const BASE = '/api/v1/conversations/conv-1';
const JOB: PanelRun = { sessionId: 'conv-1', runId: 'job-1', job: true };
const GOAL = 'Build the quarterly PDF report';
const initialChat = useChatStore.getState();
const initialJobs = useJobsStore.getState();

const jobPart = (patch: Partial<MessagePart> = {}): MessagePart =>
  ({ type: 'sub_agent', runId: 'job-1', parentRunId: 'run-root', codeTaskGoal: GOAL, status: 'running', parts: [], ...patch }) as MessagePart;

function setChat(patch: Partial<ChatSlot> = {}) {
  useChatStore.setState({
    activeSlotId: 's1',
    slots: {
      s1: {
        convId: 'conv-1',
        threadAgentId: null,
        isOwner: true,
        messages: [],
        streamingParts: [jobPart()],
        pendingApprovals: [],
        isStreaming: false,
        ...patch,
      } as unknown as ChatSlot,
    },
  });
}

const view = (patch: Partial<ActivityView> = {}): ActivityView => ({
  runId: 'job-1',
  status: 'running',
  engine: true,
  commands: [],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [],
  followups: [],
  ...patch,
});

const timeline = (patch: Partial<RunTimelineView> & { operatorStop?: unknown } = {}) => ({
  runId: 'job-1',
  parentRunId: 'run-root',
  status: 'running',
  parts: [],
  engine: null,
  approvals: [],
  children: [],
  merge: null,
  budget: null,
  ...patch,
});

const BRIEF = {
  runId: 'job-1',
  goal: 'Make a four page PDF of the third quarter',
  sent: { files: ['a.csv', 'b.csv'], sources: [], sourcesComplete: true, knowledge: { apps: [], collections: [] }, skills: [], continuesRunId: null },
  withheld: [{ what: 'credentials', reason: 'outside_sandbox' }],
  reach: { internet: 'none', sandbox: true, tools: [], allowedWithoutAsking: 0 },
};

/** The job's stream: started at 1 000 ms, optionally finished at 61 000 ms. */
const started = { event: 'RUN_STARTED', data: { type: 'RUN_STARTED', runId: 'job-1', timestamp: 1_000 }, id: '1' };
const finished = { event: 'RUN_FINISHED', data: { type: 'RUN_FINISHED', runId: 'job-1', timestamp: 61_000 }, id: '9' };

type Routes = { brief?: unknown | Error; timeline?: unknown; history?: unknown };
function route(routes: Routes = {}) {
  api.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/brief')) {
      if (routes.brief instanceof Error) throw routes.brief;
      return { data: routes.brief ?? BRIEF };
    }
    if (url.endsWith('/brief/history')) return { data: routes.history ?? { runId: 'job-1', startedAs: GOAL, changes: [] } };
    if (url.endsWith('/timeline')) return { data: routes.timeline ?? timeline() };
    if (url.includes('/steps')) return { data: { parts: [] } };
    return { data: {} };
  });
}

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useJobsStore.setState({ ...initialJobs, streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} }, true);
  useActivityPanelStore.setState({ open: false, sheetOpen: false, focusSection: null, run: null, jobSection: null });
  useAskFocusStore.setState({ pending: null });
  api.jobEvents = [];
  api.activityEvents = [];
  api.urls = [];
  api.get.mockReset();
  api.post.mockReset();
  window.history.replaceState(null, '', '/chat/?conversationId=conv-1');
  setChat();
  route();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderPanel(run: PanelRun = JOB, variant: 'pane' | 'sheet' = 'pane', onClose = vi.fn()) {
  useActivityPanelStore.getState().showJob({ sessionId: run.sessionId }, run.runId);
  return {
    onClose,
    ...render(
      <Theme>
        <ActivityPanel run={run} variant={variant} onClose={onClose} />
      </Theme>,
    ),
  };
}


const reads = (suffix: string) => api.get.mock.calls.filter(([url]) => String(url).endsWith(suffix)).length;
const progress = (kind: string, text: string, data: Record<string, unknown>, id: string) => ({
  event: 'CUSTOM',
  data: { type: 'CUSTOM', name: 'engine_progress', runId: 'job-1', value: { tool: 'code_task', taskId: 'job-1', kind, text, data } },
  id,
});

describe('F7: an ended job’s panel', () => {
  it('opens on Brief, with Brief the selected tab', async () => {
    api.jobEvents = [started, finished];
    api.activityEvents = [{ event: 'snapshot', data: view({ status: 'completed' }) }];
    setChat({ streamingParts: [jobPart({ status: 'completed' })] });
    renderPanel();
    const tabs = await screen.findByRole('tablist', { name: 'Job sections' });
    await waitFor(() => expect(within(tabs).getByRole('tab', { name: 'Brief' }).getAttribute('aria-selected')).toBe('true'));
    expect(tabs.querySelectorAll('[aria-selected="true"]')).toHaveLength(1);
    await waitFor(() => within(tabs).getByRole('tab', { name: 'Machine' }));
    fireEvent.mouseDown(within(tabs).getByRole('tab', { name: 'Machine' }), { button: 0 });
    expect(within(tabs).getByRole('tab', { name: 'Machine' }).getAttribute('aria-selected')).toBe('true');
    expect(tabs.querySelectorAll('[aria-selected="true"]')).toHaveLength(1);
  });

  it('a live one opens on Activity', async () => {
    api.jobEvents = [started];
    api.activityEvents = [{ event: 'snapshot', data: view() }];
    renderPanel();
    const tabs = await screen.findByRole('tablist', { name: 'Job sections' });
    await waitFor(() => expect(within(tabs).getByRole('tab', { name: 'Activity' }).getAttribute('aria-selected')).toBe('true'));
  });

  it('an engine job that only ran commands says they are on its Machine, not that nothing ran', async () => {
    api.jobEvents = [started, progress('command', 'mkdir -p out', { callId: 'c1', status: 'completed', exitCode: 0 }, '2'), finished];
    const command = { commandId: 'c1', command: 'mkdir -p out', status: 'completed', exitCode: 0, stdout: '', stderr: '' };
    api.activityEvents = [{ event: 'snapshot', data: view({ status: 'completed', commands: [command] as ActivityView['commands'] }) }];
    setChat({ streamingParts: [jobPart({ status: 'completed' })] });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-commands-only').textContent).toContain('Ran 1 command, listed in Machine'));
    expect(screen.queryByText('Nothing has run yet')).toBeNull();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Activity' }), { button: 0 });
    fireEvent.click(screen.getByRole('button', { name: 'Go to Machine' }));
    expect(useActivityPanelStore.getState().jobSection).toBe('machine');
  });
});

describe('F8: one timeline read for an ended job', () => {
  it('the card’s settle read serves the panel, its footer and its engine steps', async () => {
    route({ timeline: timeline({ status: 'completed' }) });
    api.jobEvents = [started, progress('tool', 'create_ticket', { callId: 't1', status: 'completed' }, '2'), finished];
    api.activityEvents = [{ event: 'snapshot', data: view({ status: 'completed' }) }];
    setChat({ streamingParts: [jobPart({ status: 'completed' })] });
    renderPanel();
    await waitFor(() => expect(useJobsStore.getState().streams['job-1']?.ended).toBe(true));
    await waitFor(() => expect(reads('/timeline')).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(reads('/timeline')).toBe(1);
  });
});

describe('F10: a loop sub-agent’s panel', () => {
  it('is titled with its goal, never "Background job" or its role id', async () => {
    const goal = 'Read the Q3 tickets and release them';
    setChat({ streamingParts: [jobPart({ codeTaskGoal: undefined, roleName: 'pipeshub-subagent-general', goal } as Partial<MessagePart>)] });
    api.jobEvents = [started];
    api.activityEvents = [{ event: 'snapshot', data: view({ engine: false }) }];
    renderPanel();
    expect(screen.getByTestId('job-panel-title').textContent).toBe(goal);
  });
});

describe('F5: Answer in chat', () => {
  it('is the high-contrast amber button', async () => {
    api.jobEvents = [started, { event: 'CUSTOM', data: { type: 'CUSTOM', name: 'approval_requested', runId: 'job-1', value: { interactionId: 'i1', toolName: 'create_ticket' } }, id: '2' }];
    api.activityEvents = [{ event: 'snapshot', data: view() }];
    renderPanel();
    const answer = await screen.findByRole('button', { name: 'Answer in chat' });
    expect(answer.getAttribute('data-accent-color')).toBe('amber');
    expect(answer.className).toContain('rt-high-contrast');
  });
});
