/**
 * CJ-3: the job details panel (classic jobs plan §4.2, D4, D5, D11, D12, D14). The Activity panel opened on a
 * job's run: its header with status and Stop, Brief, Activity, Terminal and Files with Outputs, the URL it keeps,
 * and the shared-chat viewer who sees none of the owner's sections.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
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
import { ActivityPanelHost, useActivityPanelLayout } from '../activity-panel-host';
import {
  focusJobDetails,
  jobDetailsId,
  openJob,
  openJobOf,
  useActivityPanelStore,
  type PanelRun,
} from '../activity-panel-store';
import { readJobPanelQuery, useJobPanelUrl, withJobPanelQuery } from '../use-job-panel-url';
import { jobStatusLine } from '../use-panel-job';
import { takeAskFocus, useAskFocusStore, useInteractionFocus } from '@/chat/hooks/use-interaction-focus';
import { useChatStore } from '@/chat/store';
import { useJobsStore } from '@/lib/harness-jobs/jobs-store';
import i18n from '@/lib/__tests__/test-i18n';

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
const cancelled = { event: 'RUN_ERROR', data: { type: 'RUN_ERROR', runId: 'job-1', code: 'cancelled', timestamp: 31_000 }, id: '9' };

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
  useJobsStore.setState({ ...initialJobs, streams: {}, extras: {} }, true);
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

/** A tab's visible label (Radix Themes draws it twice, once hidden, to keep the width when it turns bold). */
const tabName = (tab: HTMLElement): string | null | undefined => tab.firstElementChild?.textContent;

/** Radix's tabs change on a press (mouse down), as a person's click does in a browser. */
function openTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0 });
}

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

describe('job panel header', () => {
  it('names the job by its goal, says it is working with its clock, and offers Stop to its owner', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(61_000);
    api.jobEvents = [started];
    api.activityEvents = [{ event: 'snapshot', data: view() }];
    renderPanel();
    expect(screen.getByTestId('job-panel-title').textContent).toBe(GOAL);
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toBe('Working · 1m 00s'));
    expect(screen.getByTestId('job-panel-header').getAttribute('data-status')).toBe('working');
    expect(screen.getByTestId('job-panel-stop').getAttribute('aria-label')).toBe(`Stop job: ${GOAL}`);
    expect(api.urls).toContain(`${BASE}/runs/job-1/stream`);
  });

  it('reads Done with how long it took once its stream ended, and offers no Stop', async () => {
    api.jobEvents = [started, finished];
    setChat({ streamingParts: [jobPart({ status: 'completed' })] });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toBe('Done · 1m 00s'));
    expect(screen.queryByTestId('job-panel-stop')).toBeNull();
  });

  it('Stop is one click on the job’s own cancel route, and the job then reads Stopped by you', async () => {
    api.jobEvents = [started];
    api.post.mockResolvedValue({ data: { receipt: { accepted: true } } });
    const { rerender } = renderPanel();
    await waitFor(() => screen.getByTestId('job-panel-stop'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('job-panel-stop'));
    });
    expect(api.post).toHaveBeenCalledWith(`${BASE}/runs/job-1/cancel`, { idempotencyKey: 'stop-job-1' }, { suppressErrorToast: true });
    act(() => useJobsStore.getState().apply('job-1', cancelled, '9'));
    rerender(
      <Theme>
        <ActivityPanel run={JOB} variant="pane" onClose={vi.fn()} />
      </Theme>,
    );
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toBe('Stopped by you'));
    expect(screen.queryByTestId('job-panel-stop')).toBeNull();
  });

  it('a 409 on Stop says it had already finished, in words', async () => {
    api.jobEvents = [started];
    api.post.mockRejectedValue({ response: { status: 409 }, statusCode: 409, status: 409 });
    renderPanel();
    await waitFor(() => screen.getByTestId('job-panel-stop'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('job-panel-stop'));
    });
    expect(screen.getByTestId('job-panel-stop-gone').textContent).toBe('It had already finished');
    expect(screen.queryByTestId('job-panel-stop')).toBeNull();
  });

  it('a failed Stop says so and keeps the button', async () => {
    api.jobEvents = [started];
    api.post.mockRejectedValue(new Error('network'));
    renderPanel();
    await waitFor(() => screen.getByTestId('job-panel-stop'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('job-panel-stop'));
    });
    expect(screen.getByTestId('job-panel-stop-failed').textContent).toBe("Couldn't stop it. Try again.");
    expect(screen.getByTestId('job-panel-stop')).toBeTruthy();
  });

  it('an admin stop reads its reason from the timeline, never a code', async () => {
    api.jobEvents = [started, cancelled];
    route({ timeline: timeline({ status: 'cancelled', operatorStop: { scope: 'org', reason: 'maintenance', at: 1 } }) });
    setChat({ streamingParts: [jobPart({ status: 'cancelled' })] });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toBe('An admin stopped this: maintenance'));
  });

  it('a job waiting on an answer points to its card in the chat (D3), and hands focus there', async () => {
    api.jobEvents = [
      started,
      { event: 'CUSTOM', data: { type: 'CUSTOM', name: 'approval_requested', value: { interactionId: 'ask-1', toolName: 'send_email' } }, id: '2' },
    ];
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-header').getAttribute('data-status')).toBe('needsYou'));
    const ask = screen.getByTestId('job-panel-ask');
    expect(ask.textContent).toContain('It is waiting for your answer');
    fireEvent.click(within(ask).getByRole('button', { name: 'Answer in chat' }));
    expect(useAskFocusStore.getState().pending).toBe('ask-1');
  });
});

describe('job status words (D12)', () => {
  const t = i18n.t.bind(i18n);
  const words = (key: string, vars?: Record<string, unknown>) => t(key, vars) as string;
  it('says who or what stopped it, and why it could not finish, in plain words', () => {
    expect(jobStatusLine(t, words, { status: 'stopped', elapsedMs: 5_000 })).toBe('Stopped by you');
    expect(jobStatusLine(t, words, { status: 'stopped', elapsedMs: null, operatorStop: { reason: 'cost' } })).toBe('An admin stopped this: cost');
    expect(jobStatusLine(t, words, { status: 'couldntFinish', elapsedMs: 1_000, errorType: 'budget_exhausted' })).toMatch(/^Couldn't finish · /);
    expect(jobStatusLine(t, words, { status: 'couldntFinish', elapsedMs: 1_000, errorType: 'weird_code' })).toBe("Couldn't finish");
    expect(jobStatusLine(t, words, { status: 'needsYou', elapsedMs: 8_000 })).toBe('Needs you · 8s');
    expect(jobStatusLine(t, words, { status: 'upNext', elapsedMs: null })).toBe('Up next');
  });
});

describe('job panel sections', () => {
  it('reads the brief once when it opens and shows goal, sent, kept back and reach', async () => {
    renderPanel();
    await waitFor(() => screen.getByTestId('job-brief'));
    expect(within(screen.getByTestId('job-panel-brief')).getByTestId('job-brief-goal').textContent).toBe(BRIEF.goal);
    expect(within(screen.getByRole('tablist', { name: 'Job sections' })).getAllByRole('tab').map(tabName)).toEqual(['Brief', 'Activity', 'Outputs']);
    openTab('Brief');
    // What it was given is folded under the brief (design: "What it was given ▸").
    expect(screen.queryByTestId('job-brief-given')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'What it was given' }));
    expect(screen.getByTestId('job-brief').textContent).toContain('2 files from this chat');
    expect(screen.getByTestId('job-brief').textContent).toContain('Passwords, keys and sign-ins');
    expect(api.get.mock.calls.filter(([url]) => String(url).endsWith('/brief'))).toHaveLength(1);
    expect(api.get.mock.calls.some(([url]) => String(url).endsWith('/brief/history'))).toBe(false);
    fireEvent.click(screen.getByTestId('job-brief-history-toggle'));
    await waitFor(() => expect(screen.getByTestId('job-brief-history').textContent).toBe('Unchanged since it started'));
  });

  it('a brief that cannot be read says so, and Try again reads it again', async () => {
    route({ brief: new Error('boom') });
    renderPanel();
    await waitFor(() => screen.getByTestId('job-brief-failed'));
    expect(screen.getByTestId('job-brief-failed').textContent).toContain("Couldn't load the brief.");
    route();
    openTab('Brief');
    fireEvent.click(within(screen.getByTestId('job-brief-failed')).getByRole('button', { name: 'Try again' }));
    await waitFor(() => screen.getByTestId('job-brief'));
  });

  it('shows the design’s tabs for an engine job, one view at a time, with empty states; a tab takes focus and names the URL', async () => {
    api.jobEvents = [started];
    api.activityEvents = [{ event: 'snapshot', data: view() }];
    renderPanel();
    await waitFor(() => screen.getByTestId('job-panel-no-steps'));
    const tabs = screen.getByRole('tablist', { name: 'Job sections' });
    // BGUI-11: Outputs (what it made) between Activity (how) and Machine (its internals).
    expect(within(tabs).getAllByRole('tab').map(tabName)).toEqual(['Brief', 'Activity', 'Outputs', 'Machine']);
    // A live job opens on what it is doing.
    expect(within(tabs).getByRole('tab', { name: 'Activity' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('data-testid')).toBe('job-tab-activity');
    expect(screen.getByTestId('activity-section-commands').textContent).toContain('Terminal');
    expect(screen.getByTestId('job-panel-no-commands').textContent).toBe('No commands yet');
    openTab('Machine');
    await waitFor(() => expect(document.activeElement).toBe(within(tabs).getByRole('tab', { name: 'Machine' })));
    expect(useActivityPanelStore.getState().jobSection).toBe('machine');
    expect(within(tabs).getByRole('tab', { name: 'Machine' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('data-testid')).toBe('job-tab-machine');
  });

  it('widens on desktop and says so, and narrows again', async () => {
    api.jobEvents = [started];
    renderPanel();
    const widen = screen.getByRole('button', { name: 'Widen the panel' });
    expect(widen.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(widen);
    expect(useActivityPanelStore.getState().wide).toBe(true);
    expect(screen.getByRole('button', { name: 'Narrow the panel' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Narrow the panel' }));
    expect(useActivityPanelStore.getState().wide).toBe(false);
  });

  it('its breadcrumb leads back to every job and names this one', () => {
    api.jobEvents = [started];
    renderPanel();
    const crumbs = screen.getByRole('navigation', { name: 'Where you are' });
    expect(within(crumbs).getByRole('link', { name: 'Background agents' }).getAttribute('href')).toMatch(/^\/jobs\/?\?view=all$/);
    expect(within(crumbs).getByTestId('job-panel-title').getAttribute('aria-current')).toBe('page');
  });

  it('a loop job has no Machine, and keeps its Outputs (D1)', async () => {
    api.activityEvents = [{ event: 'snapshot', data: view({ engine: false }) }];
    renderPanel();
    await waitFor(() => screen.getByTestId('job-panel-no-steps'));
    expect(within(screen.getByRole('tablist')).getAllByRole('tab').map(tabName)).toEqual(['Brief', 'Activity', 'Outputs']);
    expect(screen.queryByTestId('activity-section-commands')).toBeNull();
  });

  it('its Outputs tab shows what it delivered as file cards, from its stream and once it ended its summary; the tab counts them', async () => {
    api.jobEvents = [
      started,
      { event: 'CUSTOM', data: { type: 'CUSTOM', name: 'engine_progress', value: { kind: 'artifact', text: 'report-q3.pdf', data: { artifactId: 'rec-1', mimeType: 'application/pdf' } } }, id: '2' },
      finished,
    ];
    route({ timeline: timeline({ status: 'completed', digest: { artifacts: [{ name: 'report-q3.pdf', recordId: 'rec-1' }, { name: 'notes.md' }] } as never }) });
    api.activityEvents = [{ event: 'snapshot', data: view({ status: 'completed' }) }];
    setChat({ streamingParts: [jobPart({ status: 'completed' })] });
    renderPanel();
    await waitFor(() => expect(screen.getAllByTestId('job-tab-outputs-count')[0].textContent).toBe('2'));
    openTab('Outputs 2');
    expect(screen.getByRole('tabpanel').getAttribute('data-testid')).toBe('job-tab-outputs');
    const cards = within(await screen.findByTestId('job-outputs-delivered')).getAllByTestId('artifact-card');
    expect(cards.map((c) => c.textContent)).toEqual([expect.stringContaining('report-q3.pdf'), expect.stringContaining('notes.md')]);
    expect(within(cards[0]).getByRole('button', { name: 'Preview artifact' })).toBeTruthy();
    expect(within(cards[0]).getByRole('button', { name: 'Download artifact' })).toBeTruthy();
    // A file with no record is named, never linked.
    expect(within(cards[1]).queryAllByRole('button')).toHaveLength(0);
  });
});

describe('a shared chat’s viewer (D14)', () => {
  it('sees the job’s title and status, and none of the owner’s sections or reads', async () => {
    setChat({ isOwner: false });
    api.jobEvents = [started];
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toMatch(/^Working/));
    expect(screen.getByTestId('job-panel-owner-only').textContent).toBe('Only the person who started this job can see its details');
    expect(screen.queryByTestId('job-panel-stop')).toBeNull();
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(screen.queryByTestId('job-brief')).toBeNull();
    expect(screen.queryByTestId('job-panel-brief')).toBeNull();
    expect(api.urls.some((u) => u.endsWith('/activity'))).toBe(false);
    expect(api.get).not.toHaveBeenCalled();
    expect(screen.queryAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Widen the panel', 'Close job details']);
  });
});

describe('before the conversation says whose it is', () => {
  it('reads none of the owner’s routes and shows neither the owner’s sections nor the viewer’s line', async () => {
    setChat({ isOwner: null });
    api.jobEvents = [started];
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('job-panel-status').textContent).toMatch(/^Working/));
    expect(api.urls.some((u) => u.endsWith('/activity'))).toBe(false);
    expect(api.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId('job-panel-owner-only')).toBeNull();
    expect(screen.queryByTestId('job-panel-body')).toBeNull();
    expect(screen.queryByTestId('job-panel-stop')).toBeNull();
  });
});

describe('the URL (D5)', () => {
  it('parses job and section, ignoring a section with no job or one it does not know', () => {
    const q = (s: string) => readJobPanelQuery(new URLSearchParams(s));
    expect(q('conversationId=c&job=j&section=machine')).toEqual({ job: 'j', section: 'machine', ask: null });
    // Links written before the tabs (Terminal, Files) open on Machine, where both now live.
    expect(q('conversationId=c&job=j&section=terminal')).toEqual({ job: 'j', section: 'machine', ask: null });
    // An old Files link named the job's files: they are its Outputs now (BGUI-11).
    expect(q('conversationId=c&job=j&section=files')).toEqual({ job: 'j', section: 'outputs', ask: null });
    expect(q('conversationId=c&job=j&section=outputs')).toEqual({ job: 'j', section: 'outputs', ask: null });
    expect(q('conversationId=c&section=machine')).toEqual({ job: null, section: null, ask: null });
    expect(q('job=j&section=nope&ask=a')).toEqual({ job: 'j', section: null, ask: 'a' });
  });

  it('writes job and section and keeps every other parameter', () => {
    expect(withJobPanelQuery('/chat/?agentId=a&conversationId=c', { runId: 'j', section: 'machine' })).toBe('/chat/?agentId=a&conversationId=c&job=j&section=machine');
    expect(withJobPanelQuery('/chat/?conversationId=c&job=j&section=files&ask=x', null, true)).toBe('/chat/?conversationId=c');
  });

  it('a link or a reload opens the job it names, at its section', () => {
    window.history.replaceState(null, '', '/chat/?conversationId=conv-1&job=job-1&section=terminal');
    renderHook(() => useJobPanelUrl(new URLSearchParams(window.location.search), { conversationId: 'conv-1', agentId: null, isMobile: false }));
    const state = useActivityPanelStore.getState();
    expect(openJobOf(state)).toEqual({ runId: 'job-1', sessionId: 'conv-1', section: 'machine' });
    expect(state.focusSection).toBe('commands');
    // An old section's name is written back as the tab it now opens.
    expect(window.location.search).toBe('?conversationId=conv-1&job=job-1&section=machine');
  });

  it('opening and closing the panel writes the URL back; a jump moves the section', () => {
    renderHook(() => useJobPanelUrl(new URLSearchParams(window.location.search), { conversationId: 'conv-1', agentId: null, isMobile: false }));
    act(() => {
      expect(openJob('job-1')).toBe(true);
    });
    expect(window.location.search).toBe('?conversationId=conv-1&job=job-1');
    act(() => useActivityPanelStore.getState().jumpTo('machine'));
    expect(window.location.search).toBe('?conversationId=conv-1&job=job-1&section=machine');
    act(() => useActivityPanelStore.getState().closeJob());
    expect(window.location.search).toBe('?conversationId=conv-1');
  });

  it('the URL losing its job (Back) closes the panel', () => {
    const { rerender } = renderHook(({ search }) => useJobPanelUrl(new URLSearchParams(search), { conversationId: 'conv-1', agentId: null, isMobile: false }), {
      initialProps: { search: 'conversationId=conv-1&job=job-1' },
    });
    expect(openJobOf(useActivityPanelStore.getState())?.runId).toBe('job-1');
    rerender({ search: 'conversationId=conv-1' });
    expect(openJobOf(useActivityPanelStore.getState())).toBeNull();
  });

  it('an ask hands focus to its card and leaves the URL; on the phone it lands on the card, not under a sheet', () => {
    window.history.replaceState(null, '', '/chat/?conversationId=conv-1&job=job-1&ask=ask-1');
    renderHook(() => useJobPanelUrl(new URLSearchParams('conversationId=conv-1&job=job-1&ask=ask-1'), { conversationId: 'conv-1', agentId: null, isMobile: true }));
    expect(useAskFocusStore.getState().pending).toBe('ask-1');
    expect(openJobOf(useActivityPanelStore.getState())).toBeNull();
    expect(window.location.search).toBe('?conversationId=conv-1');
  });

  it('on desktop an ask opens the job beside the chat and still hands focus to its card', () => {
    window.history.replaceState(null, '', '/chat/?conversationId=conv-1&job=job-1&ask=ask-1');
    renderHook(() => useJobPanelUrl(new URLSearchParams('conversationId=conv-1&job=job-1&ask=ask-1'), { conversationId: 'conv-1', agentId: null, isMobile: false }));
    expect(useAskFocusStore.getState().pending).toBe('ask-1');
    expect(openJobOf(useActivityPanelStore.getState())?.runId).toBe('job-1');
    expect(window.location.search).toBe('?conversationId=conv-1&job=job-1');
  });

  it('openJob needs a saved conversation on screen', () => {
    setChat({ convId: null });
    expect(openJob('job-1')).toBe(false);
    expect(useActivityPanelStore.getState().run).toBeNull();
  });
});

describe('layout and focus', () => {
  it('a job asked for another chat waits for it and never shows that chat’s latest run instead', () => {
    useActivityPanelStore.getState().showJob({ sessionId: 'conv-2' }, 'job-9');
    const { result } = renderHook(() => useActivityPanelLayout(false, false));
    expect(result.current.visible).toBe(false);
    act(() => useActivityPanelStore.getState().showJob({ sessionId: 'conv-1' }, 'job-1'));
    const shown = renderHook(() => useActivityPanelLayout(false, false)).result.current;
    expect(shown.visible).toBe(true);
    expect(shown.run?.runId).toBe('job-1');
  });

  it('closing a job panel clears both layouts and returns focus to the card’s Details button', async () => {
    const details = document.createElement('button');
    details.id = jobDetailsId('job-1');
    document.body.appendChild(details);
    useActivityPanelStore.getState().showJob({ sessionId: 'conv-1' }, 'job-1');
    render(
      <Theme>
        <ActivityPanelHost run={JOB} isMobile={false} />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close job details' }));
    expect(useActivityPanelStore.getState()).toMatchObject({ open: false, sheetOpen: false, run: null });
    expect(document.activeElement).toBe(details);
    details.remove();
  });

  it('Escape closes it', () => {
    const { onClose } = renderPanel();
    fireEvent.keyDown(screen.getByTestId('activity-panel'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('focus moves to the panel when it opens', async () => {
    renderPanel();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('activity-panel')));
    expect(screen.getByTestId('activity-panel').getAttribute('aria-labelledby')).toBe(screen.getByTestId('job-panel-title').id);
  });

  it('on the phone Stop is an icon with a 44 px target and a label', async () => {
    api.jobEvents = [started];
    renderPanel(JOB, 'sheet');
    await waitFor(() => screen.getByTestId('job-panel-stop'));
    const stop = screen.getByTestId('job-panel-stop');
    expect(stop.textContent).not.toContain('Stop');
    expect(stop.style.minHeight).toBe('44px');
    expect(stop.getAttribute('aria-label')).toBe(`Stop job: ${GOAL}`);
  });

  it('on the phone the sheet names it, its tabs fill the row at 44 px, and neither breadcrumb nor widen is drawn', async () => {
    api.jobEvents = [started];
    api.activityEvents = [{ event: 'snapshot', data: view() }];
    renderPanel(JOB, 'sheet');
    await waitFor(() => screen.getByTestId('job-panel-no-steps'));
    expect(screen.getByTestId('job-panel-title').textContent).toBe(GOAL);
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByTestId('job-panel-widen')).toBeNull();
    for (const tab of screen.getAllByRole('tab')) expect(tab.style.minHeight).toBe('44px');
  });

  it('focusJobDetails is quiet when no card is on screen', () => {
    expect(() => focusJobDetails('nope')).not.toThrow();
  });
});

describe('the ask hand-off (D9)', () => {
  function Card({ ids, active = false }: { ids: string[]; active?: boolean }) {
    const ref = React.useRef<HTMLDivElement>(null);
    useInteractionFocus(active, ref, null, ids);
    return (
      <div ref={ref} tabIndex={-1} data-testid={`card-${ids.join('-')}`}>
        card
      </div>
    );
  }

  it('the card holding the asked-for ask takes focus once, however late it renders', () => {
    useAskFocusStore.getState().request('ask-2');
    render(
      <>
        <Card ids={['ask-1']} />
        <Card ids={['ask-2', 'ask-3']} />
      </>,
    );
    expect(document.activeElement).toBe(screen.getByTestId('card-ask-2-ask-3'));
    expect(useAskFocusStore.getState().pending).toBeNull();
  });

  it('wins over the active card, which would otherwise take focus', () => {
    useAskFocusStore.getState().request('ask-2');
    render(
      <>
        <Card ids={['ask-1']} active />
        <Card ids={['ask-2']} />
      </>,
    );
    expect(document.activeElement).toBe(screen.getByTestId('card-ask-2'));
  });

  it('takeAskFocus leaves a request no card holds', () => {
    useAskFocusStore.getState().request('ask-9');
    expect(takeAskFocus(['ask-1'], document.body)).toBe(false);
    expect(useAskFocusStore.getState().pending).toBe('ask-9');
  });
});
