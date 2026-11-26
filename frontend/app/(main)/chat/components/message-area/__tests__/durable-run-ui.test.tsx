/**
 * U3 FC-1..FC-10: the durable run's cards after a reload, approval states and
 * authority, a durable child's own timeline (its task with a Stop of its own,
 * approvals, merge report, cost), and the workspace version list's restore.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type {
  MessagePart,
  PendingApproval,
  RunBudgetView,
  RunTimelineView,
  SSEEngineProgressEvent,
  WorkspaceVersions,
} from '../../../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn(async () => undefined) }));

const { useChatStore } = await import('../../../store');
const { ApprovalCard } = await import('../approval-card');
const { RunDetails } = await import('../../activity-panel/activity-panel');
const { AgentActivityTimeline, SubAgentGroup } = await import('../agent-activity');
const { RunCost } = await import('../run-cost');
const { WorkspaceVersionsPanel } = await import('../workspace-versions');
const { useJobsStore } = await import('../job-card/data');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const frame = (taskId: string, kind: string, text = '', data: Record<string, unknown> = {}): SSEEngineProgressEvent => ({
  tool: 'code_task',
  taskId,
  kind,
  text,
  data,
});

const BUDGET: RunBudgetView = {
  cap: { usdMicros: 5_000_000, tokens: 1_000_000 },
  committed: { usdMicros: 420_000, tokens: 1_234 },
  reserved: { usdMicros: 0, tokens: 0 },
  available: { usdMicros: 4_580_000, tokens: 998_766 },
  exhausted: false,
};

function timeline(overrides: Partial<RunTimelineView> = {}): RunTimelineView {
  return {
    runId: 'run-root',
    parentRunId: null,
    status: 'completed',
    parts: [],
    engine: null,
    approvals: [],
    children: [],
    merge: null,
    budget: BUDGET,
    ...overrides,
  };
}

function versions(head: number): WorkspaceVersions {
  return {
    workspaceId: `ws:${SESSION}`,
    head,
    versions: Array.from({ length: head }, (_, i) => head - i).map((revision) => ({
      revision,
      parentRevision: revision > 1 ? revision - 1 : null,
      reason: revision === 4 ? 'rollback' : 'turn_end',
      createdAt: revision,
      fileCount: revision,
      contentHash: `h${revision}`,
      restoredByUser: revision === 4,
    })),
  };
}

let timelines: Record<string, RunTimelineView>;
let head: number;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  timelines = { 'run-root': timeline() };
  head = 3;
  apiClient.get.mockImplementation(async (url: string) => {
    const timelineMatch = /\/runs\/([^/]+)\/timeline$/.exec(url);
    if (timelineMatch) return { data: timelines[timelineMatch[1] as string] };
    if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
    if (url.endsWith('/workspace/versions')) return { data: versions(head) };
    const runMatch = /\/runs\/([^/]+)$/.exec(url);
    if (runMatch) return { data: { run: { runId: runMatch[1], status: 'running', revision: 7 } } };
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (node: React.ReactElement) => render(h(Theme, null, node));

describe('FC-1: an engine run\'s cost and versions, in View activity (CTL-4)', () => {
  it('shows its spend, and its workspace versions once it ended', async () => {
    timelines['run-root'] = timeline({ engine: { taskId: 'run-root', frames: [frame('run-root', 'started', 'goal')], truncated: false } });
    view(h(RunDetails, { run: { sessionId: SESSION, runId: 'run-root' }, finished: true }));
    await waitFor(() => expect(screen.getByTestId('run-cost-usd').textContent).toBe('Spent $0.42 of $5.00'));
    await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(3));
    expect(apiClient.get.mock.calls.map((c) => c[0])).toContain(`/api/v1/conversations/${SESSION}/runs/run-root/timeline`);
  });

  it('versions follow the timeline\'s own status, not the activity stream', async () => {
    timelines['run-root'] = timeline({ engine: { taskId: 'run-root', frames: [], truncated: false } });
    view(h(RunDetails, { run: { sessionId: SESSION, runId: 'run-root' }, finished: false }));
    await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(3));
  });

  it('CTL-6: a loop turn that handed off to an engine child shows the session workspace versions', async () => {
    timelines['run-root'] = timeline({
      children: [{ runId: 'run-child', status: 'completed' } as never],
    });
    view(h(RunDetails, { run: { sessionId: SESSION, runId: 'run-root' }, finished: true }));
    await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(3));
  });

  it('a loop run has no workspace versions', async () => {
    view(h(RunDetails, { run: { sessionId: SESSION, runId: 'run-root' }, finished: true }));
    await waitFor(() => expect(screen.getByTestId('run-cost-usd')).toBeTruthy());
    expect(screen.queryAllByTestId('workspace-version')).toHaveLength(0);
  });

  it('SR-27: an answer saved by retired stage 1 shows its code_task call as a tool row', () => {
    const parts: MessagePart[] = [
      {
        type: 'tool_call',
        toolCallId: 'call-ct',
        toolName: 'code_task',
        displayName: 'Working in a sandbox',
        status: 'completed',
        resultPreview: '{"success": true}',
      },
      { type: 'text', content: 'The table is in q3.csv.', isFinal: true },
    ];
    const { container } = view(h(AgentActivityTimeline, { parts }));
    expect(container.textContent).toContain('Working in a sandbox');
  });
});

const approval = (overrides: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.approve.k1',
  toolCallId: 'ix.approve.k1',
  toolName: 'create_issue',
  args: '{"title":"Q3"}',
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  ...overrides,
});

describe('FC-2/FC-3: approval states and who may act', () => {
  it('FC-2: pending has both buttons; decided and expired show their outcome and no buttons', () => {
    view(h(ApprovalCard, { approval: approval(), onDecide: vi.fn() }));
    expect(screen.getByRole('button', { name: 'Allow once: Create issue' })).toBeTruthy();
    cleanup();
    for (const [status, label] of [
      ['approved', 'Approved'],
      ['denied', 'Denied'],
      ['expired', 'Expired'],
    ] as const) {
      view(h(ApprovalCard, { approval: approval({ status }), onDecide: vi.fn() }));
      const card = screen.getByRole('region', { name: 'Create issue?' });
      expect(card.getAttribute('data-status')).toBe(status);
      expect(within(card).getAllByText(label).length).toBeGreaterThan(0);
      expect(within(card).queryAllByRole('button')).toHaveLength(0);
      if (status === 'expired') expect(within(card).getByText('The task ended before anyone decided.')).toBeTruthy();
      cleanup();
    }
  });

  it('FC-3: an owner who is not the actor sees Deny only; someone with neither sees who can decide', () => {
    view(h(ApprovalCard, { approval: approval(), onDecide: vi.fn(), authority: { approve: false, deny: true } }));
    expect(screen.queryByRole('button', { name: 'Allow once: Create issue' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Deny Create issue: nothing is sent' })).toBeTruthy();
    cleanup();
    view(h(ApprovalCard, { approval: approval(), onDecide: vi.fn(), authority: { approve: false, deny: false } }));
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByText('Only the person this task runs for, or an approver, can decide.')).toBeTruthy();
  });

  it('FC-3: the server’s refusal is still shown', () => {
    view(
      h(ApprovalCard, {
        approval: approval({ errorCode: 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED' }),
        onDecide: vi.fn(),
        authority: { approve: true, deny: true },
      }),
    );
    expect(screen.getByRole('alert').textContent).toBe('Only the person this task runs for, or an approver, can approve it.');
  });
});

describe('FC-4: approval and engine parts in the activity timeline', () => {
  it('an approval part is a card row; an engine_task part is only its steps, never its goal', () => {
    const parts: MessagePart[] = [
      { type: 'engine_task', runId: 'run-root', frames: [frame('run-root', 'started', 'goal')] },
      {
        type: 'approval',
        interactionId: 'ix.approve.k1',
        toolCallId: 'ix.approve.k1',
        toolName: 'create_issue',
        args: '{}',
        status: 'denied',
        runId: 'run-root',
      },
      { type: 'text', content: 'answer', isFinal: true },
    ];
    const { container } = view(h(AgentActivityTimeline, { parts }));
    const cards = screen.getAllByTestId('approval-card');
    expect(cards).toHaveLength(1);
    expect(cards[0]?.getAttribute('data-status')).toBe('denied');
    expect(container.textContent).not.toContain('goal');
  });
});

describe('FC-5..FC-7: a durable child’s own timeline', () => {
  const child: MessagePart = {
    type: 'sub_agent',
    runId: 'run-child',
    parentRunId: 'run-root',
    roleName: 'pipeshub-subagent-jira',
    goal: 'File the Jira tickets',
    status: 'running',
    parts: [],
  };

  it('FC-5/FC-6/FC-8: opens by keyboard and shows the child’s task, approval, steps, merge report and cost', async () => {
    timelines['run-child'] = timeline({
      runId: 'run-child',
      parentRunId: 'run-root',
      status: 'completed',
      parts: [
        { type: 'engine_task', runId: 'run-child', frames: [] },
        { type: 'text', content: 'Filed the tickets.', runId: 'run-child' },
      ],
      engine: {
        taskId: 'run-child',
        frames: [
          frame('run-child', 'started', 'File tickets'),
          frame('run-child', 'command', 'jira-cli create', { callId: 'c1' }),
          frame('run-child', 'command_done', '', { callId: 'c1', exitCode: 0 }),
          frame('run-child', 'finished', 'ok', { success: true }),
        ],
        truncated: false,
      },
      approvals: [{ ...approval(), status: 'approved' }],
      merge: {
        status: 'partial',
        into: `ws:${SESSION}`,
        revision: 3,
        forkRevision: 2,
        merged: ['b.md'],
        conflicts: [{ path: 'a.md', kind: 'both_changed' }],
        repositoryConflicts: [],
      },
    });
    view(h(SubAgentGroup, { part: { ...child, status: 'completed' } }));
    const toggle = screen.getByTestId('sub-agent-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    // BGUI-2: the fold is a native button, so Enter and Space press it with no handler of its own.
    expect(toggle.tagName).toBe('BUTTON');
    fireEvent.click(toggle);
    expect(screen.getByTestId('sub-agent-toggle').getAttribute('aria-expanded')).toBe('true');
    const panel = await screen.findByTestId('child-run-panel');
    await waitFor(() => expect(within(panel).getByText('jira-cli create')).toBeTruthy());
    expect(within(panel).getByTestId('approval-card').getAttribute('data-status')).toBe('approved');
    expect(within(panel).getByText('Filed the tickets.')).toBeTruthy();
    const merge = within(panel).getByTestId('child-merge-report');
    expect(merge.textContent).toContain('1 file merged into workspace version 3.');
    expect(merge.textContent).toContain('a.md · changed on both sides');
    expect(merge.textContent).toContain("The sub-task's version of these files is in its workspace version 2.");
    expect(within(panel).getByRole('group', { name: 'Cost' }).textContent).toContain('Spent $0.42 of $5.00');
    expect(apiClient.get.mock.calls.map((c) => c[0])).toContain(`/api/v1/conversations/${SESSION}/runs/run-child/timeline`);
  });

  it('FC-7 (CJ-2): Stop sits on the collapsed card, cancels that child only, and is gone once it ended', async () => {
    apiClient.post.mockResolvedValue({ data: { receipt: {} } });
    const { rerender } = view(h(SubAgentGroup, { part: child }));
    const stop = screen.getByRole('button', { name: 'Stop job: File the Jira tickets' });
    await act(async () => {
      fireEvent.click(stop);
    });
    expect(apiClient.post.mock.calls[0]?.[0]).toBe(`/api/v1/conversations/${SESSION}/runs/run-child/cancel`);
    // UX3 ST-2: Stop is one request, keyed by the run, with no revision read first.
    expect(apiClient.post.mock.calls[0]?.[1]).toEqual({ idempotencyKey: 'stop-run-child' });
    rerender(h(Theme, null, h(SubAgentGroup, { part: { ...child, status: 'failed', errorType: 'cancelled' } })));
    expect(screen.getByTestId('job-card-status').textContent).toBe('Stopped by you');
    expect(screen.queryByRole('button', { name: 'Stop job: File the Jira tickets' })).toBeNull();
  });

  it('a legacy sub-agent (no parent named, not a durable stream) keeps its nested parts and no timeline', () => {
    view(
      h(SubAgentGroup, {
        part: { type: 'sub_agent', runId: 'legacy', roleName: 'researcher', status: 'completed', parts: [{ type: 'text', content: 'nested text' }] },
      }),
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('nested text')).toBeTruthy();
    expect(screen.queryByTestId('child-run-panel')).toBeNull();
  });

  it('a child stopped by its budget says so, in plain words', () => {
    view(h(SubAgentGroup, { part: { ...child, status: 'failed', errorType: 'budget_exhausted' } }));
    expect(screen.getByTestId('job-card-status').textContent).toBe('Stopped: out of budget');
  });

  it('QAFIX2: a child waiting on its approval card says "Needs you", and no delegating line suggests progress', () => {
    const delegating = { id: 's', status: 'executing', message: 'Delegating to Codex...', timestamp: '' };
    view(h(AgentActivityTimeline, { parts: [child], isStreaming: true, currentStatus: delegating, waiting: new Set(['run:run-child']) }));
    expect(screen.getByTestId('job-card-status').textContent).toBe('Needs you');
    expect(screen.queryByText('Delegating to Codex...')).toBeNull();
    cleanup();
    view(h(AgentActivityTimeline, { parts: [child], isStreaming: true, currentStatus: delegating, waiting: new Set<string>() }));
    // F6: handed out, with no step and nothing from its own stream yet, it is Starting; never Working, then Starting.
    expect(screen.getByTestId('job-card-status').textContent).toBe('Starting');
    expect(screen.getAllByText('Delegating to Codex...').length).toBeGreaterThan(0);
  });
});

describe('FC-8: cost', () => {
  it('USD and tokens against their caps, in a labelled group; uncapped says spent only', () => {
    view(h(RunCost, { budget: BUDGET }));
    const group = screen.getByRole('group', { name: 'Cost' });
    expect(group.textContent).toContain('Spent $0.42 of $5.00');
    expect(group.textContent).toContain('1,234 of 1,000,000 tokens');
    cleanup();
    view(h(RunCost, { budget: { ...BUDGET, cap: { usdMicros: null, tokens: null }, exhausted: true } }));
    expect(screen.getByRole('group', { name: 'Cost' }).textContent).toContain('Spent $0.42');
    expect(screen.getByText('Budget used up')).toBeTruthy();
  });
});

describe('FC-9/FC-10: workspace versions', () => {
  it('FC-9: newest first, the head current; restoring an older one confirms, then shows the new version', async () => {
    apiClient.post.mockImplementation(async () => {
      head = 4;
      return { data: { revision: 4 } };
    });
    view(h(WorkspaceVersionsPanel, { session: { sessionId: SESSION } }));
    await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(3));
    const rows = screen.getAllByTestId('workspace-version');
    expect(rows.map((r) => r.getAttribute('data-revision'))).toEqual(['3', '2', '1']);
    expect(within(rows[0] as HTMLElement).getByText('Current')).toBeTruthy();
    expect(within(rows[0] as HTMLElement).queryByRole('button')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Restore version 1' }));
    expect(apiClient.post).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    });
    expect(apiClient.post.mock.calls[0]).toEqual([
      `/api/v1/conversations/${SESSION}/workspace/versions/1/restore`,
      { baseRevision: 3, idempotencyKey: 'restore-1-on-3' },
    ]);
    await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(4));
    expect(screen.getByTestId('workspace-versions-status').textContent).toBe('Version 1 restored as version 4.');
    expect(screen.getByText('restored by you')).toBeTruthy();
  });

  it('FC-10: a moved head and a busy writer are explained; the list is read again', async () => {
    for (const [code, copy] of [
      ['HTTP_AGENT_HARNESS_WORKSPACE_CONFLICT', 'The workspace changed since this list was loaded. The list has been refreshed.'],
      ['HTTP_AGENT_HARNESS_WORKSPACE_WRITER_BUSY', 'A task is writing to this workspace. Try again when it is done.'],
    ] as const) {
      apiClient.post.mockRejectedValueOnce({ originalError: { response: { data: { error: { code } } } } });
      view(h(WorkspaceVersionsPanel, { session: { sessionId: SESSION } }));
      await waitFor(() => expect(screen.getAllByTestId('workspace-version')).toHaveLength(3));
      const reads = apiClient.get.mock.calls.length;
      fireEvent.click(screen.getByRole('button', { name: 'Restore version 2' }));
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
      });
      await waitFor(() => expect(screen.getByTestId('workspace-versions-status').textContent).toBe(copy));
      expect(apiClient.get.mock.calls.length).toBeGreaterThan(reads);
      cleanup();
    }
  });
});
