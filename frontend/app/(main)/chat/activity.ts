import { maskedReason, redactText, toApprovalReason } from './approval-preview';
import { appendTerminal, plainTerminalText, type TerminalBounds } from './code-tasks';
import { toRunModel } from './utils/run-model';
import type {
  ActivityCommand,
  ActivityFollowup,
  ActivityCommandStatus,
  ActivityEvent,
  ActivityFile,
  ActivityRunStatus,
  ActivityStepDetail,
  ActivityStepOutcome,
  ActivityStream,
  ActivityTask,
  ActivityTool,
  ActivityView,
  ChatSlot,
  DurableAgentRunHandle,
  MessagePart,
  RunTimelineView,
  ToolStepFacts,
} from './types';
import { stepFacts } from './engine-steps';
import { skillNames } from './utils/tool-display';

/**
 * The activity panel's model (UX5): the run's `snapshot` folded with live
 * `command` and `output` events. Pure, so the panel, the follow-up chips and
 * the tests share it.
 */

/** Wider than the card's terminal: the panel windows its rows, so only memory bounds it. */
export const ACTIVITY_TERMINAL_BOUNDS: TerminalBounds = { chars: 262_144, lines: 20_000 };
/** The server keeps at most this many commands in a snapshot (§3.4); live upserts keep the same bound. */
export const MAX_ACTIVITY_COMMANDS = 200;

const RUN_STATUSES: ReadonlySet<string> = new Set<ActivityRunStatus>([
  'queued',
  'running',
  'waiting',
  'completed',
  'failed',
  'cancelled',
]);
const COMMAND_STATUSES: ReadonlySet<string> = new Set<ActivityCommandStatus>([
  'running',
  'completed',
  'failed',
  'timed_out',
  'cancelled',
]);
const TOOL_STATUSES: ReadonlySet<string> = new Set<ActivityTool['status']>([
  'running',
  'completed',
  'failed',
  'awaiting_approval',
  'denied',
  'blocked',
]);
const TASK_STATUSES: ReadonlySet<string> = new Set<ActivityTask['status']>([
  'pending',
  'in_progress',
  'completed',
  'cancelled',
]);
const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled']);

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const str = (value: unknown): string | undefined => (typeof value === 'string' ? value : undefined);
const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
/** Every server string the panel shows goes through the shared token vector (QA-11), as on the approval card. */
const shown = (value: unknown): string | undefined => {
  const text = str(value);
  return text === undefined ? undefined : redactText(text).text;
};
const terminal = (value: unknown): string => plainTerminalText(shown(value) ?? '');

export function isRunFinished(status: string | undefined): boolean {
  return status !== undefined && TERMINAL_RUN_STATUSES.has(status);
}

function toCommand(value: unknown): ActivityCommand | null {
  if (!isObject(value)) return null;
  const commandId = str(value.commandId);
  if (!commandId) return null;
  const status = str(value.status) ?? 'running';
  const language = str(value.language);
  const skills = skillNames(value.skills);
  return {
    commandId,
    source: value.source === 'engine' ? 'engine' : 'run_code',
    command: terminal(value.command),
    ...(language ? { language } : {}),
    status: COMMAND_STATUSES.has(status) ? (status as ActivityCommandStatus) : 'running',
    exitCode: num(value.exitCode),
    startedAt: num(value.startedAt),
    durationMs: num(value.durationMs),
    stdout: terminal(value.stdout),
    stderr: terminal(value.stderr),
    truncated: value.truncated === true,
    liveDropped: num(value.liveDropped) ?? 0,
    hasLog: value.hasLog === true,
    ...(skills ? { skills } : {}),
    ...(value.outputMayBeLost === true ? { outputMayBeLost: true as const } : {}),
  };
}

function toTool(value: unknown): ActivityTool | null {
  if (!isObject(value)) return null;
  const toolCallId = str(value.toolCallId);
  const status = str(value.status);
  if (!toolCallId || !status || !TOOL_STATUSES.has(status)) return null;
  const displayName = shown(value.displayName);
  const refusal = isObject(value.refusal) ? value.refusal : null;
  const failure = isObject(value.failure) ? value.failure : null;
  const failureCode = failure ? str(failure.code) : undefined;
  const reason = toApprovalReason(value.reason);
  return {
    toolCallId,
    name: shown(value.name) ?? '',
    ...(displayName ? { displayName } : {}),
    status: status as ActivityTool['status'],
    ...(reason ? { reason: maskedReason(reason) } : {}),
    ...(refusal
      ? {
          refusal: {
            ...(shown(refusal.rule) ? { rule: shown(refusal.rule) } : {}),
            ...(shown(refusal.code) ? { code: shown(refusal.code) } : {}),
          },
        }
      : {}),
    ...(failure && failureCode
      ? {
          failure: {
            code: failureCode,
            ...(shown(failure.detail) ? { detail: shown(failure.detail) } : {}),
          },
        }
      : {}),
    ...shownOfCall(value),
  };
}

const APPROVAL_ANSWERS: ReadonlySet<string> = new Set(['allowed', 'declined', 'expired']);
const CHILD_STATUSES: ReadonlySet<string> = new Set(['running', 'completed', 'failed', 'cancelled']);

/** A step's detail: what the chat's step shows of a call. `args` stay raw: the renderer masks them field by field. */
function detailOf(value: Json): Partial<ActivityStepDetail> {
  const args = str(value.args);
  const argsSummary = shown(value.argsSummary);
  const resultPreview = shown(value.resultPreview);
  const resultSummary = shown(value.resultSummary);
  const artifactId = str(value.artifactId);
  return {
    ...(args !== undefined ? { args } : {}),
    ...(argsSummary ? { argsSummary } : {}),
    ...(resultPreview !== undefined ? { resultPreview } : {}),
    ...(resultSummary ? { resultSummary } : {}),
    ...(isObject(value.resultView) ? { resultView: value.resultView } : {}),
    ...(artifactId ? { artifactId } : {}),
  };
}

/** `GET …/steps/:toolCallId`'s step, checked like the snapshot; null when malformed. */
export function toStepDetail(value: unknown): ActivityStepDetail | null {
  if (!isObject(value)) return null;
  const toolCallId = str(value.toolCallId);
  const fingerprint = str(value.fingerprint);
  if (!toolCallId || !fingerprint) return null;
  return { toolCallId, fingerprint, ...detailOf(value) };
}

function outcomeOf(value: unknown): ActivityStepOutcome | undefined {
  if (!isObject(value)) return undefined;
  if (value.kind === 'empty') return { kind: 'empty' };
  const count = num(value.count);
  if (value.kind === 'found' && count !== null && count >= 0) return { kind: 'found', count };
  const text = shown(value.text);
  return value.kind === 'summary' && text ? { kind: 'summary', text } : undefined;
}

/** A step's line in the snapshot (ACTRESULTS), and any detail an older server sent with it. */
/** A step's facts as the chat reads them (`stepFacts`), its words masked as the panel masks any it shows. */
function shownStep(value: unknown): ToolStepFacts | undefined {
  const step = stepFacts(value);
  if (!step) return undefined;
  const target = shown(step.target);
  const title = shown(step.title);
  return { ...step, ...(target ? { target } : {}), ...(title ? { title } : {}) };
}

function shownOfCall(value: Json): Partial<ActivityTool> {
  const durationMs = num(value.durationMs);
  const step = shownStep(value.step);
  const outcome = outcomeOf(value.outcome);
  const detail = str(value.detail);
  const answer = str(value.approvalAnswer);
  const child = isObject(value.child) ? value.child : null;
  const childRunId = child ? str(child.runId) : undefined;
  const childStatus = child ? str(child.status) : undefined;
  const goal = child ? shown(child.codeTaskGoal) : undefined;
  const loopGoal = child ? shown(child.goal) : undefined;
  return {
    ...detailOf(value),
    ...(step ? { step } : {}),
    ...(durationMs !== null && durationMs >= 0 ? { durationMs } : {}),
    ...(outcome ? { outcome } : {}),
    ...(detail ? { detail } : {}),
    ...(answer && APPROVAL_ANSWERS.has(answer) ? { approvalAnswer: answer as ActivityTool['approvalAnswer'] } : {}),
    ...(child && childRunId && childStatus && CHILD_STATUSES.has(childStatus)
      ? {
          child: {
            runId: childRunId,
            role: shown(child.role) ?? '',
            status: childStatus as NonNullable<ActivityTool['child']>['status'],
            ...(goal ? { codeTaskGoal: goal } : {}),
            ...(loopGoal ? { goal: loopGoal } : {}),
          },
        }
      : {}),
  };
}

function toTask(value: unknown): ActivityTask | null {
  if (!isObject(value)) return null;
  const taskId = str(value.taskId);
  const status = str(value.status) ?? 'pending';
  if (!taskId) return null;
  return {
    taskId,
    title: shown(value.title) ?? '',
    status: TASK_STATUSES.has(status) ? (status as ActivityTask['status']) : 'pending',
  };
}

function toFile(value: unknown): ActivityFile | null {
  if (!isObject(value)) return null;
  const path = shown(value.path);
  if (!path) return null;
  const diff = shown(value.diff);
  return {
    path,
    change: value.change === 'added' || value.change === 'deleted' ? value.change : 'modified',
    ...(diff !== undefined ? { diff } : {}),
    ...(value.diffTruncated === true ? { diffTruncated: true } : {}),
    // The server's one classification (BGUI-11): the Outputs tab and the report's count read it.
    ...(value.delivered === true ? { delivered: true as const } : {}),
  };
}

const present = <T>(value: T | null): value is T => value !== null;

const MAX_FOLLOWUPS = 3;

/** Suggested next questions from the server, trimmed and de-duplicated; anything malformed is dropped. */
export function toFollowups(value: unknown): ActivityFollowup[] {
  const texts = list(value)
    .map((item) => (isObject(item) ? shown(item.text)?.trim() : undefined))
    .filter((text): text is string => Boolean(text));
  return [...new Set(texts)].slice(0, MAX_FOLLOWUPS).map((text) => ({ text }));
}

/** A server `ActivityView`, checked field by field: anything malformed is dropped, never trusted. */
export function toActivityView(value: unknown): ActivityView | null {
  if (!isObject(value)) return null;
  const runId = str(value.runId);
  const status = str(value.status);
  if (!runId || !status || !RUN_STATUSES.has(status)) return null;
  const model = toRunModel(value.model);
  const command = isObject(value.command) ? value.command : null;
  const slashCommand =
    command && typeof command.name === 'string' && typeof command.version === 'string'
      ? {
          name: command.name,
          version: command.version,
          expandedChars: num(command.expandedChars) ?? 0,
          owner: str(command.owner) ?? 'you',
        }
      : null;
  return {
    runId,
    status: status as ActivityRunStatus,
    engine: value.engine === true,
    commands: list(value.commands).map(toCommand).filter(present),
    omittedCommands: num(value.omittedCommands) ?? 0,
    tools: list(value.tools).map(toTool).filter(present),
    tasks: list(value.tasks).map(toTask).filter(present),
    files: list(value.files).map(toFile).filter(present),
    followups: toFollowups(value.followups),
    ...(model && value.personRan !== true ? { model } : {}),
    ...(slashCommand ? { slashCommand } : {}),
    ...(value.personRan === true ? { personRan: true } : {}),
  };
}

/** One SSE event of `GET …/runs/:runId/activity`, or null when it is not one the panel knows. */
export function toActivityEvent(name: string, data: unknown): ActivityEvent | null {
  switch (name) {
    case 'snapshot': {
      const view = toActivityView(data);
      return view ? { type: 'snapshot', view } : null;
    }
    case 'command': {
      const command = toCommand(data);
      return command ? { type: 'command', command } : null;
    }
    case 'output': {
      if (!isObject(data)) return null;
      const commandId = str(data.commandId);
      if (!commandId) return null;
      const stream: ActivityStream = data.stream === 'stderr' ? 'stderr' : 'stdout';
      const dropped = num(data.dropped);
      if (dropped !== null) return { type: 'output', commandId, stream, dropped };
      const text = shown(data.text);
      return text === undefined ? null : { type: 'output', commandId, stream, text };
    }
    case 'end':
      return { type: 'end', status: (isObject(data) ? str(data.status) : undefined) ?? '' };
    default:
      return null;
  }
}

function placeholder(commandId: string): ActivityCommand {
  return {
    commandId,
    source: 'run_code',
    command: '',
    status: 'running',
    exitCode: null,
    startedAt: null,
    durationMs: null,
    stdout: '',
    stderr: '',
    truncated: false,
    liveDropped: 0,
    hasLog: false,
  };
}

/** A live upsert carries no output of its own: what the panel already streamed stays. */
function mergeCommand(local: ActivityCommand | undefined, incoming: ActivityCommand): ActivityCommand {
  if (!local) return incoming;
  return {
    ...incoming,
    command: incoming.command || local.command,
    ...((incoming.skills ?? local.skills) ? { skills: incoming.skills ?? local.skills } : {}),
    stdout: incoming.stdout || local.stdout,
    stderr: incoming.stderr || local.stderr,
    truncated: incoming.truncated || local.truncated,
    liveDropped: Math.max(incoming.liveDropped, local.liveDropped),
    ...(incoming.outputMayBeLost || local.outputMayBeLost ? { outputMayBeLost: true as const } : {}),
  };
}

function withCommands(view: ActivityView, commands: ActivityCommand[]): ActivityView {
  const excess = commands.length - MAX_ACTIVITY_COMMANDS;
  if (excess <= 0) return { ...view, commands };
  return { ...view, commands: commands.slice(excess), omittedCommands: view.omittedCommands + excess };
}

function upsert(view: ActivityView, commandId: string, change: (command: ActivityCommand) => ActivityCommand): ActivityView {
  const at = view.commands.findIndex((c) => c.commandId === commandId);
  if (at < 0) return withCommands(view, [...view.commands, change(placeholder(commandId))]);
  return { ...view, commands: view.commands.map((c, i) => (i === at ? change(c) : c)) };
}

function appendOutput(command: ActivityCommand, stream: ActivityStream, text: string, cut = false): ActivityCommand {
  const { terminal, truncated } = appendTerminal(command[stream], plainTerminalText(text), ACTIVITY_TERMINAL_BOUNDS);
  return { ...command, [stream]: terminal, truncated: command.truncated || truncated || cut };
}

/**
 * Adds an event to the ones waiting to be folded (PF-17). Output to the same command and stream as the last one
 * waiting is joined to it, so a burst (10 MB of a build's log) is one append to the terminal, not thousands that
 * each copy it; the joined text keeps a bounded tail (at most four times what the terminal shows). Joined text
 * also strips an escape sequence split across two events, which one-by-one folding left behind.
 */
export function enqueueActivityEvent(pending: ActivityEvent[], event: ActivityEvent): void {
  const last = pending[pending.length - 1];
  if (
    event.type !== 'output' ||
    event.text === undefined ||
    last?.type !== 'output' ||
    last.text === undefined ||
    last.commandId !== event.commandId ||
    last.stream !== event.stream
  ) {
    pending.push(event);
    return;
  }
  let text = last.text + event.text;
  let truncated = last.truncated ?? false;
  // Twice the bound kept: escape sequences and control characters are stripped only when folded. Cut only past
  // twice that, so the copy a cut makes is paid once per hundreds of events, not on each one.
  const keep = 2 * ACTIVITY_TERMINAL_BOUNDS.chars;
  if (text.length > 2 * keep) {
    text = text.slice(text.length - keep);
    truncated = true;
  }
  pending[pending.length - 1] = { ...last, text, ...(truncated ? { truncated } : {}) };
}

/**
 * Folds one activity event (FE-1). A snapshot replaces the view, except that a
 * command still running keeps the output streamed so far when the snapshot
 * carries none of it (a journal snapshot can lag the live frames).
 */
export function reduceActivity(view: ActivityView | null, event: ActivityEvent): ActivityView | null {
  switch (event.type) {
    case 'snapshot': {
      if (!view || view.runId !== event.view.runId) return event.view;
      const before = new Map(view.commands.map((c) => [c.commandId, c]));
      return {
        ...event.view,
        commands: event.view.commands.map((c) =>
          c.status === 'running' ? mergeCommand(before.get(c.commandId), c) : c,
        ),
      };
    }
    case 'command':
      if (!view) return view;
      return upsert(view, event.command.commandId, (local) => mergeCommand(local, event.command));
    case 'output':
      if (!view) return view;
      return upsert(view, event.commandId, (command) =>
        event.dropped !== undefined
          ? { ...command, liveDropped: command.liveDropped + event.dropped }
          : appendOutput(command, event.stream, event.text ?? '', event.truncated ?? false),
      );
    case 'end':
      if (!view) return view;
      return RUN_STATUSES.has(event.status) ? { ...view, status: event.status as ActivityRunStatus } : view;
    default:
      return view;
  }
}

export type ActivityAnnouncement =
  | { kind: 'commandStarted'; command: string }
  | { kind: 'commandFinished'; command: string; status: ActivityCommandStatus; exitCode: number | null }
  | { kind: 'approvalNeeded'; tool: string }
  | { kind: 'runFinished'; status: ActivityRunStatus };

/**
 * What changed for a screen reader between two views (FE-4): commands that
 * started or finished, approvals that became needed, the run's end. Output is
 * never announced. The first view announces nothing: it is history.
 */
export function activityAnnouncements(prev: ActivityView | null, next: ActivityView | null): ActivityAnnouncement[] {
  if (!prev || !next || prev.runId !== next.runId) return [];
  const out: ActivityAnnouncement[] = [];
  const before = new Map(prev.commands.map((c) => [c.commandId, c]));
  for (const command of next.commands) {
    const was = before.get(command.commandId);
    const label = command.command || command.commandId;
    if (!was && command.status === 'running') out.push({ kind: 'commandStarted', command: label });
    if (command.status !== 'running' && (!was || was.status === 'running')) {
      out.push({ kind: 'commandFinished', command: label, status: command.status, exitCode: command.exitCode });
    }
  }
  const toolsBefore = new Map(prev.tools.map((tool) => [tool.toolCallId, tool.status]));
  for (const tool of next.tools) {
    if (tool.status === 'awaiting_approval' && toolsBefore.get(tool.toolCallId) !== 'awaiting_approval') {
      out.push({ kind: 'approvalNeeded', tool: tool.displayName || tool.name });
    }
  }
  if (!isRunFinished(prev.status) && isRunFinished(next.status)) out.push({ kind: 'runFinished', status: next.status });
  return out;
}

type RunSlot = Pick<ChatSlot, 'convId' | 'threadAgentId' | 'durableRun' | 'lastDurableRun' | 'messages'> &
  Partial<Pick<ChatSlot, 'isOwner' | 'durable'>>;

/**
 * The durable run that wrote the latest answer, as saved: the message's own
 * run id when the server records one, else its engine task's run (U3 saves
 * the engine run's id on the `engine_task` part). Older answers never count.
 */
export function latestAnswerRunId(messages: RunSlot['messages']): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role !== 'assistant') continue;
    return messageRunId(message.metadata?.custom);
  }
  return null;
}

/**
 * The durable run that wrote one saved answer, from its metadata: its run id, else its engine task's run. None for
 * an answer a fork copied: its run belongs to the original conversation, whose routes alone read it.
 */
export function messageRunId(custom: unknown): string | null {
  const { durableRunId, persistedParts, copied } = (custom ?? {}) as {
    durableRunId?: unknown;
    persistedParts?: unknown;
    copied?: unknown;
  };
  if (copied === true) return null;
  if (typeof durableRunId === 'string' && durableRunId) return durableRunId;
  const parts = Array.isArray(persistedParts) ? (persistedParts as MessagePart[]) : [];
  for (let p = parts.length - 1; p >= 0; p -= 1) {
    const part = parts[p];
    if (part?.type === 'engine_task' && part.runId) return part.runId;
  }
  return null;
}

/**
 * The run the activity panel shows: the run in flight, else the last one this
 * session followed, else (after a reload) the one that wrote the latest answer,
 * unless the conversation is someone else's: its runs are not the viewer's (D-5).
 */
export function activityRun(slot: RunSlot | undefined): DurableAgentRunHandle | null {
  // A viewer follows the owner's run live (D14), but its activity, timeline and follow-ups are the owner's (F3).
  if (!slot || slot.isOwner === false) return null;
  if (slot.durableRun) return slot.durableRun;
  if (slot.lastDurableRun) return slot.lastDurableRun;
  if (!slot.convId) return null;
  const runId = latestAnswerRunId(slot.messages);
  if (!runId) return null;
  return { ...(slot.threadAgentId ? { agentId: slot.threadAgentId } : {}), sessionId: slot.convId, runId };
}

export function activityRunId(slot: RunSlot | undefined): string | null {
  return activityRun(slot)?.runId ?? null;
}

const TEST_COMMAND =
  /(?:^|[\s;&|(])(?:pytest|py\.test|python3? -m (?:pytest|unittest)|jest|vitest|mocha|rspec|phpunit|tox|go test|cargo test|dotnet test|(?:npm|pnpm|yarn|bun)(?: run)? test|mvn(?: \S+)* (?:test|verify)|(?:\.\/)?gradlew?(?: \S+)* test|make(?: \S+)* (?:test|check))(?=$|[\s;&|)])/;

/** A command that runs a test suite, by the runners people use. */
export function isTestCommand(command: string): boolean {
  return TEST_COMMAND.test(command.trim());
}

export type EngineActionChip = { kind: 'openFiles' } | { kind: 'runTests'; command: string };

export interface EngineRunSummary {
  commands: string[];
  changedFiles: number;
}

/** What an engine run did, for its action chips: from the activity view when the panel has one. */
export function engineSummaryFromView(view: ActivityView): EngineRunSummary {
  return { commands: view.commands.map((c) => c.command), changedFiles: view.files.length };
}

/** The same summary from the run's timeline (U3's engine frames), so chips cost no stream. */
export function engineSummaryFromTimeline(timeline: RunTimelineView): EngineRunSummary | null {
  if (!timeline.engine) return null;
  const files = new Set<string>();
  const commands: string[] = [];
  for (const frame of timeline.engine.frames) {
    if (frame.kind === 'command' && frame.text) commands.push(frame.text);
    if ((frame.kind === 'file' || frame.kind === 'diff') && frame.text) files.add(frame.text);
  }
  return { commands, changedFiles: files.size };
}

/** FU-7: chips from what an engine run did, computed here with no model call. */
export function engineActionChips(summary: EngineRunSummary | null): EngineActionChip[] {
  if (!summary) return [];
  const chips: EngineActionChip[] = [];
  if (summary.changedFiles > 0) chips.push({ kind: 'openFiles' });
  const tests = summary.commands.filter(isTestCommand);
  const last = tests[tests.length - 1];
  if (last) chips.push({ kind: 'runTests', command: last.trim() });
  return chips;
}

/** A duration in the reader's locale: "850 ms", "4.2 sec", "2 min 5 sec". */
export function formatDuration(ms: number, locale: string): string {
  const unit = (value: number, name: 'millisecond' | 'second' | 'minute', digits = 0) =>
    new Intl.NumberFormat(locale, { style: 'unit', unit: name, unitDisplay: 'short', maximumFractionDigits: digits }).format(value);
  if (ms < 1_000) return unit(Math.round(ms), 'millisecond');
  if (ms < 60_000) return unit(ms / 1_000, 'second', 1);
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1_000);
  return seconds ? `${unit(minutes, 'minute')} ${unit(seconds, 'second')}` : unit(minutes, 'minute');
}

/** A byte count in the reader's locale: "1 byte", "512 bytes", "12 kB", "1.5 MB". */
export function formatBytes(bytes: number, locale: string): string {
  const unit = (value: number, name: 'byte' | 'kilobyte' | 'megabyte', display: 'short' | 'long' = 'short') =>
    new Intl.NumberFormat(locale, { style: 'unit', unit: name, unitDisplay: display, maximumFractionDigits: 1 }).format(value);
  // The short byte unit is singular in English ("403 byte"); the long one is pluralised in every locale.
  if (bytes < 1_024) return unit(bytes, 'byte', 'long');
  if (bytes < 1_048_576) return unit(bytes / 1_024, 'kilobyte');
  return unit(bytes / 1_048_576, 'megabyte');
}
