/**
 * CTRESULTS: while a code task ran, the chat showed none of its child's work and its card opened onto
 * nothing. A child's steps (an engine's commands, files and tool calls, a loop sub-agent's calls) now nest under
 * its card, live from the parent's stream and after a reload from the child's own timeline, drawn by the chat's
 * own rows; opening the card always says something.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { SSEEvent } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import type { ActivityTool, MessagePart, RunTimelineView, SSEEngineProgressEvent } from '../../../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn() }));

const { useChatStore } = await import('../../../store');
const { useJobsStore } = await import('@/lib/harness-jobs/jobs-store');
const { AgentActivityTimeline, SubAgentGroup } = await import('../agent-activity');
const { createAGUIEventHandler } = await import('../../../agui-event-handler');
const { StepList } = await import('../../activity-panel/step-row');
const { forgetStepDetails } = await import('../../activity-panel/use-step-detail');

const h = React.createElement;
const SESSION = 'conv-1';
const ROOT = 'run-root';
const CHILD = 'run-child';
const GOAL = 'Write hello.py that prints 2+2 and run it';
const initialState = useChatStore.getState();

const line = (kind: string, text: string, data: Record<string, unknown> = {}, seq = 0): SSEEngineProgressEvent & { turnId: string; seq: number } => ({
  tool: 'code_task',
  taskId: CHILD,
  kind,
  text,
  data,
  turnId: 'trn_1',
  seq,
});

/** An engine child's work as a recorded opencode child journaled it, with the gateway result the worker now pairs. */
const ENGINE_LINES = [
  line('started', GOAL, {}, 1),
  line('file', 'hello.py', { change: 'modified' }, 2),
  line('command', 'python3 hello.py', { callId: 'c1' }, 3),
  line('output', '4\n', { callId: 'c1', stream: 'stdout' }, 4),
  line('command_done', '', { callId: 'c1', exitCode: 0, status: 'completed' }, 5),
  line('tool', 'mcp_e2esaas_lookup_note', { callId: 't1', displayName: 'E2Esaas · lookup_note' }, 6),
  line('tool_result', 'mcp_e2esaas_lookup_note', { callId: 't1', status: 'completed', result: 'Private note: the review is due on Friday.' }, 7),
  line('version', '2', { revision: 2, reason: 'turn_end' }, 8),
  line('diff', 'hello.py', { change: 'added', diff: '--- a/hello.py\n+++ b/hello.py\n@@ -0,0 +1 @@\n+print(2+2)\n' }, 9),
];

const frame = (type: string, fields: Record<string, unknown> = {}): SSEEvent => ({ event: type, data: { type, ...fields } }) as SSEEvent;
const progress = (value: Record<string, unknown>, runId = CHILD): SSEEvent => frame('CUSTOM', { name: 'engine_progress', value, runId });

/** The parent's stream up to its code task's child starting, as Node projects it. */
const HANDED_OFF: SSEEvent[] = [
  frame('RUN_STARTED', { runId: ROOT, threadId: SESSION }),
  frame('TOOL_CALL_START', { toolCallId: 'call_ct', toolCallName: 'code_task', runId: ROOT }),
  frame('TOOL_CALL_ARGS', { toolCallId: 'call_ct', delta: JSON.stringify({ goal: GOAL }), runId: ROOT }),
  frame('STEP_STARTED', { stepName: 'sub_agent:pipeshub-subagent', runId: ROOT }),
  frame('RUN_STARTED', { runId: CHILD, parentRunId: ROOT, codeTaskGoal: GOAL }),
];

/** The live parts a durable stream builds from `events`. */
function liveParts(events: SSEEvent[]): MessagePart[] {
  let parts: MessagePart[] = [];
  const handle = createAGUIEventHandler({ onParts: (next: MessagePart[]) => (parts = next) } as never, undefined, {
    replaceRestartedMessages: true,
  });
  for (const event of events) handle(event);
  return parts;
}

function timeline(patch: Partial<RunTimelineView> = {}): RunTimelineView {
  return {
    runId: CHILD,
    parentRunId: ROOT,
    status: 'completed',
    parts: [],
    engine: null,
    approvals: [],
    children: [],
    merge: null,
    budget: null,
    ...patch,
  };
}

/** A recorded engine child after it finished, as `GET …/runs/:child/timeline` reads it. */
const ENGINE_TIMELINE = timeline({
  parts: [
    { type: 'engine_task', runId: CHILD, taskId: CHILD, frames: ENGINE_LINES },
    { type: 'text', content: 'the output is 4', runId: CHILD, isFinal: true },
  ],
  engine: { taskId: CHILD, frames: ENGINE_LINES, truncated: false },
});

/** The saved answer after a reload: the code task's call and its card, no child steps of its own. */
const SAVED: MessagePart[] = [
  {
    type: 'tool_call',
    toolCallId: 'call_ct',
    toolName: 'code_task',
    status: 'completed',
    runId: ROOT,
    argsSummary: `Code Task — goal: "${GOAL}"`,
    resultPreview: '{"output":"the output is 4","success":true,"workspace":{"merged":["hello.py"],"status":"merged"}}',
  },
  { type: 'sub_agent', runId: CHILD, parentRunId: ROOT, roleName: 'pipeshub-subagent', codeTaskGoal: GOAL, status: 'completed', parts: [] },
];

let childTimeline: Promise<{ data: RunTimelineView }>;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  // A job's timeline is read once and shared (ADR-CJ-01): each case starts with none read.
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
  childTimeline = Promise.resolve({ data: ENGINE_TIMELINE });
  forgetStepDetails();
  apiClient.get.mockImplementation((url: string) => {
    if (url.endsWith(`/runs/${CHILD}/timeline`)) return childTimeline;
    if (url.endsWith('/interactions/authority')) return Promise.resolve({ data: { approve: true, deny: true } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (parts: MessagePart[], isStreaming = false) =>
  render(h(Theme, null, h(AgentActivityTimeline, { parts, isStreaming })));

/** Opens the code task's card and returns its panel. */
async function openCard(): Promise<HTMLElement> {
  fireEvent.click(screen.getByTestId('sub-agent-toggle'));
  return waitFor(() => screen.getByTestId('child-run-panel'));
}

/** The panel's rows once its steps have drawn. */
async function stepsIn(panel: HTMLElement): Promise<void> {
  await waitFor(() => expect(within(panel).getAllByTestId('engine-command').length).toBeGreaterThan(0));
}

/** Every row of an engine child: the command with its output, the file with its diff, the tool with its result. */
function expectEngineRows(panel: HTMLElement): void {
  const command = within(panel).getByTestId('engine-command');
  expect(command.textContent).toContain('python3 hello.py');
  fireEvent.click(within(command).getByRole('button'));
  expect(within(command).getByTestId('engine-command-output').textContent).toBe('4');

  const file = within(panel).getByTestId('engine-file');
  expect(file.textContent).toContain('Created');
  expect(file.textContent).toContain('hello.py');
  expect(within(panel).queryByTestId('code-task-diff')).toBeNull();
  fireEvent.click(within(file).getByRole('button'));
  expect(within(file).getByTestId('code-task-diff').textContent).toContain('+print(2+2)');

  // The gateway call's result, drawn by the chat's own tool result and collapsed until opened.
  expect(panel.textContent).not.toContain('the review is due on Friday');
  fireEvent.click(within(panel).getByRole('button', { name: /Lookup note/ }));
  expect(within(panel).getByTestId('tool-result').textContent).toContain('the review is due on Friday');
}

describe('CTRESULTS: a running code task shows its child\'s steps', () => {
  it('nests the engine child\'s progress under its card, not at the answer\'s root, and names it a durable child', () => {
    const parts = liveParts([...HANDED_OFF, ...ENGINE_LINES.map((value) => progress(value))]);
    expect(parts.some((part) => part.type === 'engine_task')).toBe(false);
    const card = parts.find((part) => part.type === 'sub_agent');
    expect(card).toMatchObject({ runId: CHILD, parentRunId: ROOT, status: 'running' });
    expect(card?.parts?.[0]).toMatchObject({ type: 'engine_task', taskId: CHILD });
    expect(card?.parts?.[0]?.frames).toHaveLength(ENGINE_LINES.length);
  });

  it('a step said again on a resumed stream is not drawn twice', () => {
    const lines = ENGINE_LINES.map((value) => progress(value));
    const parts = liveParts([...HANDED_OFF, ...lines, ...lines]);
    expect(parts.find((part) => part.type === 'sub_agent')?.parts?.[0]?.frames).toHaveLength(ENGINE_LINES.length);
  });

  it('the card says what the child is doing now, and opening it shows its steps before its timeline loads', async () => {
    childTimeline = new Promise(() => {});
    const running = ENGINE_LINES.slice(0, 3).map((value) => progress(value));
    view(liveParts([...HANDED_OFF, ...running]), true);
    expect(screen.getByTestId('job-card-now').textContent).toBe('Running python3 hello.py');
    // One row for the hand-off: the card is the code task.
    expect(screen.queryByText('Code Task')).toBeNull();
    const panel = await openCard();
    await stepsIn(panel);
    expect(within(panel).getByTestId('engine-command').textContent).toContain('Running');
    expect(within(panel).getByTestId('engine-file').textContent).toContain('hello.py');
  });

  it('opened live, the card shows the command\'s output, the file\'s diff and the tool\'s result', async () => {
    childTimeline = new Promise(() => {});
    view(liveParts([...HANDED_OFF, ...ENGINE_LINES.map((value) => progress(value))]), true);
    const panel = await openCard();
    await stepsIn(panel);
    expectEngineRows(panel);
  });

  it('a loop sub-agent\'s calls nest under its card with their results, even those said after it ended', () => {
    const parts = liveParts([
      ...HANDED_OFF.slice(0, 4),
      frame('RUN_STARTED', { runId: CHILD, parentRunId: ROOT }),
      frame('TOOL_CALL_START', { toolCallId: 'k1', toolCallName: 'web_search', runId: CHILD }),
      frame('TOOL_CALL_ARGS', { toolCallId: 'k1', delta: '{"query":"release date"}', argsSummary: 'Search: "release date"', runId: CHILD }),
      frame('RUN_FINISHED', { runId: CHILD, parentRunId: ROOT }),
      frame('TOOL_CALL_END', { toolCallId: 'k1', runId: CHILD }),
      frame('TOOL_CALL_RESULT', { toolCallId: 'k1', content: 'Q3: September 30', status: 'completed', resultSummary: 'Found 3 results', runId: CHILD }),
      frame('TOOL_CALL_START', { toolCallId: 'k2', toolCallName: 'fetch_url', runId: CHILD }),
    ]);
    const card = parts.find((part) => part.type === 'sub_agent');
    expect(card?.parts?.map((part) => [part.toolCallId, part.status, part.resultSummary])).toEqual([
      ['k1', 'completed', 'Found 3 results'],
      ['k2', 'running', undefined],
    ]);
    expect(parts.filter((part) => part.type === 'tool_call').map((part) => part.toolCallId)).toEqual(['call_ct']);
  });
});

describe('CTRESULTS: after a reload the card opens onto the same steps and the child\'s answer', () => {
  it('reads the engine child\'s timeline: command, file, tool result and answer, as live', async () => {
    view(SAVED);
    expect(screen.queryByText('Code Task')).toBeNull();
    const panel = await openCard();
    await stepsIn(panel);
    expectEngineRows(panel);
    expect(within(panel).getByTestId('child-run-answer').textContent).toContain('the output is 4');
    expect(panel.textContent).not.toContain('"success":true');
  });

  it('a loop sub-agent\'s timeline shows its calls, each result collapsed until opened', async () => {
    childTimeline = Promise.resolve({
      data: timeline({
        parts: [
          { type: 'tool_call', toolCallId: 'k1', toolName: 'web_search', status: 'completed', argsSummary: 'Search: "release"', resultSummary: 'Found 3 results', resultPreview: 'Q3: September 30' },
          { type: 'text', content: 'It ships on September 30.', isFinal: true, runId: CHILD },
        ],
      }),
    });
    view([{ ...SAVED[1]!, codeTaskGoal: undefined, roleName: 'researcher' }]);
    const panel = await openCard();
    await waitFor(() => expect(within(panel).getByTestId('child-run-answer').textContent).toContain('September 30'));
    expect(within(panel).queryByTestId('tool-result')).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: /search/i }));
    expect(within(panel).getByTestId('tool-result').textContent).toContain('Found 3 results');
  });

  it('never opens onto nothing: loading, then a plain line when the child ran no steps', async () => {
    let finish: (value: { data: RunTimelineView }) => void = () => {};
    childTimeline = new Promise((resolve) => (finish = resolve));
    view(SAVED);
    const panel = await openCard();
    expect(panel.textContent).toContain('Loading the timeline');
    finish({ data: timeline() });
    await waitFor(() => expect(within(panel).getByTestId('child-run-empty').textContent).toBe('This task finished without running any steps.'));
  });

  it('a child row the stream has nothing for yet says so instead of opening empty', () => {
    useChatStore.setState({ activeSlotId: null });
    view([{ ...SAVED[1]!, status: 'running', parentRunId: undefined }], true);
    fireEvent.click(screen.getByRole('button', { name: /Code task/ }));
    expect(screen.getByTestId('sub-agent-empty').textContent).toBe('No steps yet. They show here as the task works.');
  });
});

describe('CTRESULTS: the Activity panel opens a code task onto the same rows', () => {
  it('shows the engine child\'s command output and file diff with the chat\'s rows', async () => {
    const run = { sessionId: SESSION, runId: ROOT };
    const tools: ActivityTool[] = [
      { toolCallId: 'call_ct', name: 'code_task', status: 'completed', child: { runId: CHILD, role: 'coder', status: 'completed', codeTaskGoal: GOAL } },
    ];
    render(h(Theme, null, h(StepList, { tools, run })));
    fireEvent.click(screen.getByTestId('activity-tool-toggle'));
    const steps = await waitFor(() => screen.getByTestId('activity-child-steps'));
    await waitFor(() => expect(within(steps).getAllByTestId('engine-command').length).toBe(1));
    fireEvent.click(within(within(steps).getByTestId('engine-command')).getByRole('button'));
    expect(within(steps).getByTestId('engine-command-output').textContent).toBe('4');
    fireEvent.click(within(within(steps).getByTestId('engine-file')).getByRole('button'));
    expect(within(steps).getByTestId('code-task-diff').textContent).toContain('+print(2+2)');
  });
});
