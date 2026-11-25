/**
 * UX5 FE-1, FU-7 and the panel's run: the activity reducer over the §3.4
 * events, the engine action chips, which run the panel shows, and the stream
 * client's reconnect, end and refusal paths over a faked `streamSSEGet`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SSEGetOptions } from '@/lib/api/streaming';
import { StreamError } from '@/lib/api/stream-errors';
import type {
  ActivityCommand,
  ActivityEvent,
  ActivityView,
  ChatSlot,
  MessagePart,
  RunTimelineView,
  SSEEngineProgressEvent,
} from '../types';

const sse = vi.hoisted(() => ({ calls: [] as Array<{ url: string; options: SSEGetOptions }>, plan: [] as Array<(o: SSEGetOptions) => void> }));
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  streamSSEGet: async (url: string, options: SSEGetOptions) => {
    sse.calls.push({ url, options });
    const step = sse.plan.shift();
    step?.(options);
  },
}));

const {
  activityAnnouncements,
  activityRun,
  activityRunId,
  engineActionChips,
  engineSummaryFromTimeline,
  engineSummaryFromView,
  formatDuration,
  isTestCommand,
  latestAnswerRunId,
  MAX_ACTIVITY_COMMANDS,
  reduceActivity,
  toActivityEvent,
  toFollowups,
} = await import('../activity');
const { ChatApi } = await import('../api');
const { apiClient } = await import('@/lib/api');
const { followRunActivity, activityUrl } = await import('../hooks/use-run-activity');

afterEach(() => {
  sse.calls.length = 0;
  sse.plan.length = 0;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const command = (patch: Partial<ActivityCommand> = {}): ActivityCommand => ({
  commandId: 'cmd-1',
  source: 'run_code',
  command: 'pytest -q',
  status: 'running',
  exitCode: null,
  startedAt: 1_000,
  durationMs: null,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
  ...patch,
});

const view = (patch: Partial<ActivityView> = {}): ActivityView => ({
  runId: 'run-1',
  status: 'running',
  engine: false,
  commands: [],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [],
  followups: [],
  ...patch,
});

/** Events as the wire carries them, through the same parser the stream uses. */
function wire(name: string, data: unknown): ActivityEvent {
  const event = toActivityEvent(name, data);
  if (!event) throw new Error(`not an activity event: ${name}`);
  return event;
}

const fold = (events: ActivityEvent[], start: ActivityView | null = null) =>
  events.reduce<ActivityView | null>((state, event) => reduceActivity(state, event), start);

describe('FE-1: the activity reducer', () => {
  it('upserts commands by id and appends their output as plain text, stream by stream', () => {
    const state = fold([
      wire('snapshot', view()),
      wire('command', command()),
      wire('output', { commandId: 'cmd-1', stream: 'stdout', text: '\u001b[32m3 passed\u001b[0m\r\n' }),
      wire('output', { commandId: 'cmd-1', stream: 'stderr', text: 'warning: slow\n' }),
      wire('output', { commandId: 'cmd-1', stream: 'stdout', text: 'done\n' }),
      wire('command', command({ status: 'completed', exitCode: 0, durationMs: 1_250 })),
    ]);
    expect(state?.commands).toHaveLength(1);
    const [only] = state!.commands;
    expect(only).toMatchObject({ status: 'completed', exitCode: 0, durationMs: 1_250 });
    expect(only!.stdout).toBe('3 passed\ndone\n');
    expect(only!.stderr).toBe('warning: slow\n');
  });

  it('a newer snapshot replaces the view; a running command keeps what streamed when the snapshot has none of it', () => {
    const live = fold([
      wire('snapshot', view({ commands: [command({ commandId: 'old', status: 'completed', exitCode: 1, stdout: 'x' })] })),
      wire('command', command()),
      wire('output', { commandId: 'cmd-1', stream: 'stdout', text: 'line 1\n' }),
    ]);
    const next = reduceActivity(
      live,
      wire('snapshot', view({ commands: [command()], tasks: [{ taskId: 'task-1', title: 'Fix the test', status: 'in_progress' }] })),
    );
    expect(next?.commands.map((c) => c.commandId)).toEqual(['cmd-1']);
    expect(next?.commands[0]?.stdout).toBe('line 1\n');
    expect(next?.tasks).toHaveLength(1);

    const finished = reduceActivity(
      next,
      wire('snapshot', view({ status: 'completed', commands: [command({ status: 'completed', exitCode: 0, stdout: 'tail from the journal\n' })] })),
    );
    expect(finished?.commands[0]?.stdout).toBe('tail from the journal\n');
    expect(finished?.status).toBe('completed');
  });

  it('counts output that was not shown live, and an output before its command makes a running entry', () => {
    const state = fold([
      wire('snapshot', view()),
      wire('output', { commandId: 'cmd-9', stream: 'stdout', text: 'early\n' }),
      wire('output', { commandId: 'cmd-9', stream: 'stdout', dropped: 4_096 }),
    ]);
    expect(state?.commands[0]).toMatchObject({ commandId: 'cmd-9', status: 'running', stdout: 'early\n', liveDropped: 4_096 });
  });

  it('keeps at most the server bound of commands, counting the dropped ones', () => {
    const many = Array.from({ length: MAX_ACTIVITY_COMMANDS }, (_, i) => command({ commandId: `c-${i}`, status: 'completed' }));
    const state = fold([wire('snapshot', view({ commands: many })), wire('command', command({ commandId: 'c-new' }))]);
    expect(state?.commands).toHaveLength(MAX_ACTIVITY_COMMANDS);
    expect(state?.commands[0]?.commandId).toBe('c-1');
    expect(state?.omittedCommands).toBe(1);
  });

  it('refuses malformed events and ignores deltas before the first snapshot', () => {
    expect(toActivityEvent('snapshot', { runId: 'r', status: 'exploded' })).toBeNull();
    expect(toActivityEvent('snapshot', 'nope')).toBeNull();
    expect(toActivityEvent('output', { stream: 'stdout', text: 'x' })).toBeNull();
    expect(toActivityEvent('mystery', {})).toBeNull();
    const parsed = toActivityEvent('snapshot', { runId: 'r', status: 'running', commands: [{ nope: 1 }, { commandId: 'a' }] });
    expect(parsed?.type === 'snapshot' && parsed.view.commands.map((c) => c.commandId)).toEqual(['a']);
    expect(reduceActivity(null, wire('command', command()))).toBeNull();
  });

  it('`end` sets the run status', () => {
    expect(fold([wire('snapshot', view()), wire('end', { status: 'failed' })])?.status).toBe('failed');
  });
});

describe('FE-4 (model): what the live region may say', () => {
  it('announces starts, finishes with exit code, approvals and the end, never output', () => {
    const start = view();
    const started = reduceActivity(start, wire('command', command()))!;
    expect(activityAnnouncements(start, started)).toEqual([{ kind: 'commandStarted', command: 'pytest -q' }]);

    const printed = reduceActivity(started, wire('output', { commandId: 'cmd-1', stream: 'stdout', text: 'a\nb\n' }))!;
    expect(activityAnnouncements(started, printed)).toEqual([]);

    const finished = reduceActivity(printed, wire('command', command({ status: 'failed', exitCode: 2 })))!;
    expect(activityAnnouncements(printed, finished)).toEqual([
      { kind: 'commandFinished', command: 'pytest -q', status: 'failed', exitCode: 2 },
    ]);

    const waiting = { ...finished, tools: [{ toolCallId: 't-1', name: 'slack.send', displayName: 'Send to Slack', status: 'awaiting_approval' as const }] };
    expect(activityAnnouncements(finished, waiting)).toEqual([{ kind: 'approvalNeeded', tool: 'Send to Slack' }]);
    expect(activityAnnouncements(waiting, { ...waiting, status: 'completed' })).toEqual([{ kind: 'runFinished', status: 'completed' }]);
  });

  it('says nothing for the first snapshot: it is history', () => {
    expect(activityAnnouncements(null, view({ commands: [command({ status: 'completed', exitCode: 0 })] }))).toEqual([]);
  });
});

describe('FU-7: engine action chips', () => {
  it.each([
    ['pytest -q tests/unit', true],
    ['python -m pytest', true],
    ['npm test', true],
    ['npm run test -- --watch=false', true],
    ['pnpm test', true],
    ['yarn test', true],
    ['npx jest src', true],
    ['npx vitest run', true],
    ['go test ./...', true],
    ['cargo test', true],
    ['mvn -q test', true],
    ['./gradlew test', true],
    ['make test', true],
    ['cd app && pytest', true],
    ['ls -la', false],
    ['echo test', false],
    ['cat pytest.ini', false],
    ['npm install', false],
    ['git commit -m "fix tests"', false],
  ])('%s is a test command: %s', (cmd, expected) => {
    expect(isTestCommand(cmd)).toBe(expected);
  });

  it('offers "Open the changed files" when files changed and "Run the tests again" with the last test command', () => {
    expect(engineActionChips({ commands: ['ls', 'pytest -q', 'npm test', 'git status'], changedFiles: 2 })).toEqual([
      { kind: 'openFiles' },
      { kind: 'runTests', command: 'npm test' },
    ]);
    expect(engineActionChips({ commands: ['ls'], changedFiles: 0 })).toEqual([]);
    expect(engineActionChips(null)).toEqual([]);
  });

  it('reads what the run did from its timeline frames or its activity view', () => {
    const frame = (kind: string, text: string): SSEEngineProgressEvent => ({ tool: 'code_task', taskId: 'run-1', kind, text });
    const timeline = {
      runId: 'run-1',
      engine: { taskId: 'run-1', truncated: false, frames: [frame('command', 'pytest'), frame('file', 'a.py'), frame('diff', 'a.py'), frame('diff', 'b.py')] },
    } as unknown as RunTimelineView;
    expect(engineSummaryFromTimeline(timeline)).toEqual({ commands: ['pytest'], changedFiles: 2 });
    expect(engineSummaryFromTimeline({ ...timeline, engine: null })).toBeNull();
    expect(engineSummaryFromView(view({ commands: [command()], files: [{ path: 'a.py', change: 'modified' }] }))).toEqual({
      commands: ['pytest -q'],
      changedFiles: 1,
    });
  });
});

describe('the run the panel shows', () => {
  const answer = (custom: Record<string, unknown>) => ({ role: 'assistant' as const, id: 'm', content: [], metadata: { custom } });
  const slot = (patch: Partial<ChatSlot> = {}) =>
    ({ convId: 'conv-1', threadAgentId: null, durableRun: null, lastDurableRun: null, messages: [], isOwner: true, ...patch }) as Pick<
      ChatSlot,
      'convId' | 'threadAgentId' | 'durableRun' | 'lastDurableRun' | 'messages' | 'isOwner'
    >;
  const enginePart: MessagePart = { type: 'engine_task', taskId: 'run-engine', runId: 'run-engine', frames: [] };

  it('is the run in flight, else the last one this session followed', () => {
    expect(activityRunId(slot({ durableRun: { sessionId: 'conv-1', runId: 'live' }, lastDurableRun: { sessionId: 'conv-1', runId: 'old' } }))).toBe('live');
    expect(activityRunId(slot({ lastDurableRun: { agentId: 'a-1', sessionId: 'conv-1', runId: 'old' } }))).toBe('old');
  });

  it('after a reload: the latest answer\'s own run id, else its engine task\'s run; older answers never count', () => {
    expect(activityRun(slot({ threadAgentId: 'a-1', messages: [answer({ durableRunId: 'saved' })] as ChatSlot['messages'] }))).toEqual({
      agentId: 'a-1',
      sessionId: 'conv-1',
      runId: 'saved',
    });
    expect(latestAnswerRunId([answer({ persistedParts: [{ type: 'text', content: 'x' }, enginePart] })] as ChatSlot['messages'])).toBe('run-engine');
    const older = [answer({ durableRunId: 'older' }), { role: 'user', id: 'u', content: [] }, answer({})] as ChatSlot['messages'];
    expect(latestAnswerRunId(older)).toBeNull();
    expect(activityRunId(slot())).toBeNull();
  });

  it('none from saved messages in someone else\'s conversation (D-5)', () => {
    expect(activityRunId(slot({ isOwner: false, messages: [answer({ durableRunId: 'theirs' })] as ChatSlot['messages'] }))).toBeNull();
  });
});

describe('the activity stream client', () => {
  const RUN = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

  async function follow() {
    const controller = new AbortController();
    const events: ActivityEvent[] = [];
    const connections: string[] = [];
    const done = followRunActivity(RUN, {
      signal: controller.signal,
      onEvent: (event) => events.push(event),
      onConnection: (connection) => connections.push(connection),
    });
    return { controller, events, connections, done };
  }

  it('opens the owner route, reconnects with backoff after a drop, takes the fresh snapshot, and stops at `end`', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    sse.plan.push(
      (o) => {
        o.onEvent({ event: 'snapshot', data: view() });
        o.onEvent({ event: 'output', data: { commandId: 'c', stream: 'stdout', text: 'x' } });
        o.onError(new Error('network'));
      },
      (o) => {
        o.onEvent({ event: 'snapshot', data: view({ status: 'completed' }) });
        o.onEvent({ event: 'end', data: { status: 'completed' } });
      },
    );
    const run = await follow();
    await vi.advanceTimersByTimeAsync(1_000);
    await run.done;
    expect(sse.calls.map((c) => c.url)).toEqual([activityUrl(RUN), activityUrl(RUN)]);
    expect(activityUrl(RUN)).toBe('/api/v1/agents/agent-1/conversations/conv-1/runs/run-1/activity');
    expect(run.connections).toEqual(['connecting', 'live', 'reconnecting', 'live', 'ended']);
    expect(run.events.map((e) => e.type)).toEqual(['snapshot', 'output', 'snapshot', 'end']);
  });

  it('a 404 (no such run, not the owner, flag off) is "unavailable", without retrying', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    sse.plan.push((o) => o.onError(new StreamError('Not found', 404)));
    const run = await follow();
    await run.done;
    expect(sse.calls).toHaveLength(1);
    expect(run.connections).toEqual(['connecting', 'unavailable']);
  });

  it('stops when aborted (the panel closed)', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    sse.plan.push((o) => o.onError(new Error('network')));
    const run = await follow();
    await vi.advanceTimersByTimeAsync(0);
    run.controller.abort();
    await run.done;
    expect(sse.calls).toHaveLength(1);
  });
});

describe('FU: the follow-ups route client', () => {
  it('reads the run\'s follow-ups route with waitMs, without an error toast, and passes the abort signal', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { followups: [{ text: ' Compare with last quarter ' }] } });
    const aborted = new AbortController();
    const read = await ChatApi.getRunFollowups({ agentId: 'agent-1', sessionId: 'conv-1', runId: 'run/1' }, 12_000, aborted.signal);
    expect(read).toEqual([{ text: 'Compare with last quarter' }]);
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/agents/agent-1/conversations/conv-1/runs/run%2F1/followups', {
      params: { waitMs: 12_000 },
      signal: aborted.signal,
      suppressErrorToast: true,
    });
  });

  it('trusts no shape: malformed items are dropped, duplicates merged, at most three kept', () => {
    expect(toFollowups(undefined)).toEqual([]);
    expect(toFollowups({ text: 'not a list' })).toEqual([]);
    expect(toFollowups([{ text: 'A' }, { text: ' A ' }, null, { text: 7 }, { text: '  ' }, 'B', { text: 'B' }, { text: 'C' }, { text: 'D' }])).toEqual([
      { text: 'A' },
      { text: 'B' },
      { text: 'C' },
    ]);
  });
});

describe('durations', () => {
  it('reads in the locale', () => {
    expect(formatDuration(850, 'en-US')).toBe('850 ms');
    expect(formatDuration(4_200, 'en-US')).toBe('4.2 sec');
    expect(formatDuration(125_000, 'en-US')).toBe('2 min 5 sec');
    expect(formatDuration(4_200, 'de-DE')).toBe('4,2 Sek.');
  });
});
