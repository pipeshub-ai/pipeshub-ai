import {
  CanonicalJsonError,
  canonicalSha256,
} from '../contracts/canonical-json';
import type { PipeshubCommandFinishedEvent } from '../contracts/generated/ext/pipeshub/events/x-pipeshub-command-finished';
import type { PipeshubEngineProgressEvent } from '../contracts/generated/ext/pipeshub/events/x-pipeshub-engine-progress';
import type { ToolIntentEvent } from '../contracts/generated/core/events/tool-intent';
import type { WorkspaceCommittedEvent } from '../contracts/generated/core/events/workspace-committed';
import type { PipeshubToolStep } from '../contracts/generated/ext/pipeshub/tool-step';
import { RunStatus } from '../domain/execution';
import { Followup } from '../domain/followups';
import { JournalEvent } from '../domain/journal';
import { LiveChannel, LiveFrame } from '../ports/live-bus';
import { codePoints } from './text-filter';
import {
  ApprovalReason,
  ApprovalStatus,
  ApprovalView,
  CallView,
  ChildStatus,
  COMMAND_RUN,
  CommandView,
  ModelLabel,
  RunEnd,
  TaskView,
  Timeline,
  TimelineBuilder,
} from './timeline';
import { classifiedFiles } from './job-files';
import { commandSkills } from './skill-use';
import { MessagePart, toolPart } from './transcript';

/**
 * What a run did in its sandbox, as the activity panel shows it to the run's
 * owner (UX5): commands with their output, tool calls with approvals, the
 * plan and the files it changed. Folded from the journal (pure, so a reload
 * and a second client see the same view) and topped up from the activity
 * bus while a command runs (`applyLive`, `mergeLive`).
 */

export const MAX_ACTIVITY_COMMANDS = 200;
/** Per stream: the newest output, as the worker's record keeps it. */
export const MAX_COMMAND_TAIL_CHARS = 4_000;
export const MAX_COMMAND_TEXT_CHARS = 2_000;
const RUN_CODE = 'run_code';

export type ActivityFrameKind =
  'command_started' | 'command_output' | 'command_finished' | 'followups';
export type ActivityFrame = LiveFrame<ActivityFrameKind>;

/** `harness:activity:<runId>` (`contracts/harness/vectors/activity-frames.json`). */
export const ACTIVITY_LIVE_CHANNEL: LiveChannel<ActivityFrame> = Object.freeze({
  name: 'activity',
  kinds: new Set<ActivityFrameKind>([
    'command_started',
    'command_output',
    'command_finished',
    'followups',
  ]),
});

export type ActivityRunStatus =
  'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled';
export type ActivityCommandStatus =
  'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled';
export type CommandStream = 'stdout' | 'stderr';

export interface ActivityCommand {
  commandId: string;
  source: 'run_code' | 'engine';
  command: string;
  language?: string;
  status: ActivityCommandStatus;
  exitCode: number | null;
  startedAt: number | null;
  durationMs: number | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  liveDropped: number;
  hasLog: boolean;
  /** The skills the command read (FIX-SKILLSHOW), in order; absent when it read none. */
  skills?: string[];
  /** R32ENGFU-FU-1: its engine can lose a command's output, so an empty output is unverified. */
  outputMayBeLost?: true;
}

export type ActivityToolStatus =
  | 'running'
  | 'completed'
  | 'failed'
  | 'awaiting_approval'
  | 'denied'
  | 'blocked';

export interface ActivityTool {
  toolCallId: string;
  name: string;
  displayName?: string;
  status: ActivityToolStatus;
  reason?: ApprovalReason;
  refusal?: { rule?: string; code?: string };
  /** A failed call (ERR-UX): its code and the redacted detail the model read, shown to the run's owner only. */
  failure?: { code: string; detail?: string };
  /** What the call asked for, in words (the chat step's `argsSummary`). */
  argsSummary?: string;
  /**
   * Which page, query, pattern or file, and what came of it (WP-115): the chat step's `step`, less the links its
   * result returned, which only the answer's citations read.
   */
  step?: Omit<PipeshubToolStep, 'sources'>;
  durationMs?: number;
  /** A finished call's outcome in a line, from its result (ACTRESULTS). */
  outcome?: StepOutcome;
  /**
   * The fingerprint of the step's detail (`ActivityStepDetail`), which the panel reads from
   * `…/runs/:runId/steps/:toolCallId` when the step is opened: a snapshot, sent on every journal advance, never
   * carries a result again. It changes when the detail does, so a client's copy is kept until then.
   */
  detail?: string;
  /** How a person answered the call's approval. */
  approvalAnswer?: ApprovalAnswer;
  /** The child run the call handed work to (`code_task`, a sub-agent); its steps are its own run's. */
  child?: ActivityChild;
}

export type ApprovalAnswer = 'allowed' | 'declined' | 'expired';

export type StepOutcome =
  | { kind: 'summary'; text: string }
  | { kind: 'found'; count: number }
  | { kind: 'empty' };

/**
 * What the chat's own step shows of a call (`toolPart`), with the same bounds: the panel is the owner's (D-5),
 * so it shows its owner what their chat does and nothing more (ACTRESULTS).
 */
export interface ActivityStepDetail {
  toolCallId: string;
  fingerprint: string;
  args?: string;
  argsSummary?: string;
  resultPreview?: string;
  resultSummary?: string;
  resultView?: Record<string, unknown>;
  artifactId?: string;
}

export interface ActivityChild {
  runId: string;
  role: string;
  status: ChildStatus;
  codeTaskGoal?: string;
  /** A loop sub-agent's goal (CJFIX-B): what the step names it by, never its role id. */
  goal?: string;
}

export type ActivityTask = Pick<TaskView, 'taskId' | 'title' | 'status'>;

export type FileChange = 'added' | 'modified' | 'deleted';

export interface ActivityFile {
  path: string;
  change: FileChange;
  diff?: string;
  diffTruncated?: boolean;
  /** Published as one of the run's artifacts (`job-files.ts`); absent: a working file. */
  delivered?: true;
}

export type { Followup };

export interface ActivityView {
  runId: string;
  status: ActivityRunStatus;
  engine: boolean;
  /** Oldest first, at most `MAX_ACTIVITY_COMMANDS`; older ones counted in `omittedCommands`. */
  commands: ActivityCommand[];
  omittedCommands: number;
  tools: ActivityTool[];
  tasks: ActivityTask[];
  files: ActivityFile[];
  followups: Followup[];
  /** The model the run calls, as its journal names it (MA1); absent while it names none. */
  model?: ModelLabel;
  /** The /command the run started from (SLASH1). */
  command?: CommandView;
  /** A person's `/run` (SLASH3): they ran it, not the agent, and no model was asked. */
  personRan?: true;
  /** C4.10 `ui.terminal` off: commands are listed by name, their output is never sent. */
  outputHidden?: true;
}

export interface ActivityFoldOptions {
  runId: string;
  /**
   * Engine `output` lines as command output. Off when the activity bus
   * serves live output, which carries the same text and would repeat it.
   */
  journalOutput?: boolean;
}

export interface ActivityOptions extends ActivityFoldOptions {
  /** The run's status now; the journal's terminal event, when there is one, wins. */
  runStatus?: RunStatus;
  /** The run document's suggested next questions. */
  followups?: Followup[];
}

type CommandFinishedData = PipeshubCommandFinishedEvent['data'];
type EngineProgressData = PipeshubEngineProgressEvent['data'];

const COMMAND_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'timed_out',
  'cancelled',
]);
const FILE_CHANGES: ReadonlySet<string> = new Set([
  'added',
  'modified',
  'deleted',
]);
const RUN_STATUS: Readonly<Record<RunStatus, ActivityRunStatus>> = {
  queued: 'queued',
  running: 'running',
  recovering: 'running',
  cancelling: 'running',
  waiting_approval: 'waiting',
  paused: 'waiting',
  needs_reconciliation: 'waiting',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};
const TERMINAL: ReadonlySet<ActivityRunStatus> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

const str = (value: unknown): string | undefined =>
  typeof value === 'string' ? value : undefined;
const int = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) ? value : null;
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

function head(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return codePoints(text).slice(0, limit).join('');
}

/** The newest `MAX_COMMAND_TAIL_CHARS` of `text`, and whether anything was cut. */
function tail(text: string): { text: string; cut: boolean } {
  if (text.length <= MAX_COMMAND_TAIL_CHARS) return { text, cut: false };
  const points = codePoints(text);
  if (points.length <= MAX_COMMAND_TAIL_CHARS) return { text, cut: false };
  return { text: points.slice(-MAX_COMMAND_TAIL_CHARS).join(''), cut: true };
}

function appended(
  command: ActivityCommand,
  stream: CommandStream,
  text: string,
): ActivityCommand {
  const bounded = tail(command[stream] + text);
  return {
    ...command,
    [stream]: bounded.text,
    truncated: command.truncated || bounded.cut,
  };
}

const commandStatus = (value: unknown): ActivityCommandStatus =>
  COMMAND_STATUSES.has(str(value) ?? '')
    ? (value as ActivityCommandStatus)
    : 'failed';

const timestamp = (ts: string): number | null => {
  const at = Date.parse(ts);
  return Number.isNaN(at) ? null : at;
};

const running = (
  commandId: string,
  source: ActivityCommand['source'],
  command: string,
  startedAt: number | null,
  language?: string,
): ActivityCommand => ({
  commandId,
  source,
  command: head(command, MAX_COMMAND_TEXT_CHARS),
  ...(language !== undefined ? { language } : {}),
  status: 'running',
  exitCode: null,
  startedAt,
  durationMs: null,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
});

function finishedCommand(data: CommandFinishedData): ActivityCommand {
  return {
    commandId: data.commandId,
    source: data.source,
    command: head(data.command, MAX_COMMAND_TEXT_CHARS),
    ...(data.language !== undefined ? { language: data.language } : {}),
    status: commandStatus(data.status),
    exitCode: data.exitCode ?? null,
    startedAt: data.startedAt,
    durationMs: data.durationMs ?? null,
    stdout: data.stdout.tail,
    stderr: data.stderr.tail,
    truncated: data.stdout.truncated || data.stderr.truncated,
    liveDropped: data.liveDropped,
    hasLog: data.log !== undefined,
    ...(data.outputMayBeLost === true
      ? { outputMayBeLost: true as const }
      : {}),
  };
}

/** `run_code`'s program: what the model asked it to run, when its arguments say. */
function runCodeArguments(call: CallView | undefined): {
  code: string;
  language?: string;
} {
  let args: Record<string, unknown>;
  try {
    args = record(JSON.parse(call?.argsJson ?? '{}'));
  } catch {
    args = {};
  }
  const language = str(args['language']);
  return {
    code: str(args['code']) ?? '',
    ...(language !== undefined ? { language } : {}),
  };
}

const isRunCode = (call: CallView | undefined, toolId: string): boolean =>
  call?.name === RUN_CODE || toolId.endsWith(`/${RUN_CODE}`);

function endStatus(end: RunEnd): ActivityRunStatus {
  if (end.status === 'quarantined') return 'failed';
  return end.status;
}

function toolStatus(
  call: CallView,
  approval: ApprovalView | undefined,
  ended: boolean,
): ActivityToolStatus {
  if (call.status === 'blocked') {
    return approval?.status === 'denied' ||
      call.blockedBy === 'interaction_denied'
      ? 'denied'
      : 'blocked';
  }
  if (call.status === 'completed' || call.status === 'failed') {
    return call.status;
  }
  if (approval?.status === 'pending') return 'awaiting_approval';
  if (approval?.status === 'denied') return 'denied';
  if (approval?.status === 'expired') return 'blocked';
  return ended ? 'failed' : 'running';
}

/** An engine's own call held for approval (E1): known only by its interaction. */
function engineApprovalStatus(approval: ApprovalView): ActivityToolStatus {
  switch (approval.status) {
    case 'pending':
      return 'awaiting_approval';
    case 'denied':
      return 'denied';
    case 'expired':
      return 'blocked';
    case 'approved':
    case 'closed':
      return 'completed';
  }
}

const ANSWERS: Partial<Record<ApprovalStatus, ApprovalAnswer>> = {
  approved: 'allowed',
  denied: 'declined',
  expired: 'expired',
};

const DETAIL_FIELDS = [
  'args',
  'argsSummary',
  'resultPreview',
  'resultSummary',
  'resultView',
  'artifactId',
] as const;
const MAX_OUTCOME_CHARS = 160;
const EMPTY_RESULTS: ReadonlySet<string> = new Set([
  '',
  '[]',
  '{}',
  'null',
  '""',
]);

type DetailFields = Omit<ActivityStepDetail, 'toolCallId' | 'fingerprint'>;

function withFingerprint(
  toolCallId: string,
  fields: DetailFields,
): ActivityStepDetail {
  const all = DETAIL_FIELDS.map((field) => fields[field] ?? null);
  let digest: string;
  try {
    digest = canonicalSha256(all);
  } catch (error) {
    if (!(error instanceof CanonicalJsonError)) throw error;
    // A tool's result view the canonical form refuses (a reserved key): its text fields still mark a change.
    digest = canonicalSha256(
      all.map((value) => (typeof value === 'string' ? value : null)),
    );
  }
  return { toolCallId, fingerprint: digest.slice(0, 16), ...fields };
}

/** A call's detail, from `toolPart`: the one function that builds the chat's step. */
function callDetail(callId: string, part: MessagePart): ActivityStepDetail {
  const fields: Record<string, unknown> = {};
  for (const field of DETAIL_FIELDS) {
    if (part[field] !== undefined) fields[field] = part[field];
  }
  return withFingerprint(callId, fields);
}

/** An engine's own call held for approval: what the card showed is all there is. */
function approvalDetail(approval: ApprovalView): ActivityStepDetail {
  return withFingerprint(approval.toolCallId, {
    args: approval.args,
    ...(approval.argsSummary !== undefined
      ? { argsSummary: approval.argsSummary }
      : {}),
  });
}

/** A completed step's outcome in a line: the summary's first line, else what its view or preview says. */
function outcomeOf(detail: ActivityStepDetail): StepOutcome | undefined {
  const line = detail.resultSummary
    ?.split('\n')
    .map((text) => text.replace(/^[-*]\s+/, '').trim())
    .find(Boolean);
  if (line !== undefined) {
    const points = codePoints(line);
    return {
      kind: 'summary',
      text:
        points.length > MAX_OUTCOME_CHARS
          ? `${points.slice(0, MAX_OUTCOME_CHARS - 1).join('')}…`
          : line,
    };
  }
  const view = detail.resultView;
  if (view?.['kind'] === 'records' && Array.isArray(view['rows'])) {
    const total = view['total'];
    const rows = (view['rows'] as unknown[]).length;
    return {
      kind: 'found',
      count:
        typeof total === 'number' && Number.isFinite(total)
          ? Math.max(total, rows)
          : rows,
    };
  }
  if (
    detail.resultPreview !== undefined &&
    EMPTY_RESULTS.has(detail.resultPreview.trim())
  )
    return { kind: 'empty' };
  return undefined;
}

/** A step's line in the snapshot: its summary of arguments, its outcome and its detail's fingerprint. */
function stepLine(
  detail: ActivityStepDetail,
  completed: boolean,
): Partial<ActivityTool> {
  const outcome = completed ? outcomeOf(detail) : undefined;
  const hasDetail = DETAIL_FIELDS.some(
    (field) => field !== 'argsSummary' && detail[field] !== undefined,
  );
  return {
    ...(detail.argsSummary !== undefined
      ? { argsSummary: detail.argsSummary }
      : {}),
    ...(outcome !== undefined ? { outcome } : {}),
    ...(hasDetail ? { detail: detail.fingerprint } : {}),
  };
}

function withoutSources(
  step: PipeshubToolStep,
): Omit<PipeshubToolStep, 'sources'> {
  const { sources: _links, ...line } = step;
  return line;
}

/** One step's detail for the panel's open step; null when the run has no such step. */
export function stepDetail(
  timeline: Timeline,
  toolCallId: string,
): ActivityStepDetail | null {
  const call = timeline.turns
    .flatMap((turn) => turn.calls)
    .find((view) => view.callId === toolCallId);
  if (call !== undefined) {
    return call.hidden || call.streamsAnswer === true
      ? null
      : callDetail(call.callId, toolPart(call, timeline.runId));
  }
  const approval = timeline.approvals.find(
    (view) => view.toolCallId === toolCallId,
  );
  return approval !== undefined ? approvalDetail(approval) : null;
}

function childrenByCall(builder: TimelineBuilder): Map<string, ActivityChild> {
  const byCall = new Map<string, ActivityChild>();
  for (const child of builder.timeline.children) {
    const call = builder.callForInvocation(child.invocationId);
    if (call === undefined) continue;
    byCall.set(call.callId, {
      runId: child.runId,
      role: child.role,
      status: child.status,
      ...(child.codeTaskGoal !== undefined
        ? { codeTaskGoal: child.codeTaskGoal }
        : {}),
      ...(child.goal !== undefined ? { goal: child.goal } : {}),
    });
  }
  return byCall;
}

function toolsOf(builder: TimelineBuilder, ended: boolean): ActivityTool[] {
  const { turns, approvals, runId } = builder.timeline;
  const byCall = new Map(approvals.map((view) => [view.toolCallId, view]));
  const children = childrenByCall(builder);
  const tools: ActivityTool[] = [];
  for (const call of turns.flatMap((turn) => turn.calls)) {
    if (call.hidden || call.streamsAnswer === true) continue;
    const approval = byCall.get(call.callId);
    byCall.delete(call.callId);
    const answer = approval ? ANSWERS[approval.status] : undefined;
    const child = children.get(call.callId);
    const part = toolPart(call, runId);
    const took = part['durationMs'];
    tools.push({
      toolCallId: call.callId,
      name: call.name,
      ...(call.displayName !== undefined
        ? { displayName: call.displayName }
        : {}),
      ...stepLine(callDetail(call.callId, part), call.status === 'completed'),
      ...(call.step !== undefined ? { step: withoutSources(call.step) } : {}),
      ...(typeof took === 'number' ? { durationMs: took } : {}),
      ...(answer !== undefined ? { approvalAnswer: answer } : {}),
      ...(child !== undefined ? { child } : {}),
      status: toolStatus(call, approval, ended),
      ...(approval?.reason !== undefined ? { reason: approval.reason } : {}),
      ...(call.blockedBy !== undefined
        ? { refusal: { rule: call.blockedBy } }
        : {}),
      ...(call.shadow !== undefined ? { dryRun: { ...call.shadow } } : {}),
      ...(call.errorCode !== undefined
        ? {
            failure: {
              code: call.errorCode,
              ...(call.detail ? { detail: call.detail } : {}),
            },
          }
        : {}),
    });
  }
  for (const approval of byCall.values()) {
    if (builder.call(approval.toolCallId) !== undefined) continue;
    const answer = ANSWERS[approval.status];
    tools.push({
      toolCallId: approval.toolCallId,
      name: approval.toolName,
      ...(approval.displayName !== undefined
        ? { displayName: approval.displayName }
        : {}),
      ...stepLine(approvalDetail(approval), false),
      ...(answer !== undefined ? { approvalAnswer: answer } : {}),
      status: engineApprovalStatus(approval),
      ...(approval.reason !== undefined ? { reason: approval.reason } : {}),
    });
  }
  return tools;
}

/** The newest commands, oldest first, and how many older ones were left out. */
function capped(
  commands: ActivityCommand[],
  omitted: number,
): { commands: ActivityCommand[]; omittedCommands: number } {
  const over = Math.max(0, commands.length - MAX_ACTIVITY_COMMANDS);
  return { commands: commands.slice(over), omittedCommands: omitted + over };
}

/**
 * One file's key, whichever event names it: a commit names `/out/a.csv`, the
 * engine's diff line `out/a.csv` (BGUI-11: a job listed each changed file
 * twice, 14 for 7).
 */
export const workspacePath = (path: string): string => path.replace(/^\/+/, '');

/**
 * A run's change to a path once a later commit changed it again, relative to
 * where the run started: a file it added stays added, and one it added and then
 * deleted is no change at all (null).
 */
export function laterChange(
  earlier: FileChange,
  later: FileChange,
): FileChange | null {
  if (later === 'deleted') return earlier === 'added' ? null : 'deleted';
  return earlier === 'added' ? 'added' : 'modified';
}

/** The fold `foldActivity` runs, kept by a stream that applies each journal page as it comes. */
export class ActivityFold {
  private readonly builder: TimelineBuilder;
  private readonly commands = new Map<string, ActivityCommand>();
  private readonly files = new Map<string, ActivityFile>();

  constructor(private readonly options: ActivityFoldOptions) {
    this.builder = new TimelineBuilder(options.runId);
  }

  /** The timeline folded alongside, so a reader needing both folds the journal once. */
  get timeline(): Timeline {
    return this.builder.timeline;
  }

  apply(event: JournalEvent): void {
    this.builder.apply(event);
    if (event.runId !== this.options.runId || event.data === undefined) return;
    switch (event.type) {
      case 'tool.intent':
        this.intent(event, event.data as ToolIntentEvent['data']);
        return;
      case 'tool.outcome':
        this.outcome(event.data);
        return;
      case 'x.pipeshub.command.finished': {
        const finished = finishedCommand(event.data as CommandFinishedData);
        this.commands.set(finished.commandId, finished);
        return;
      }
      case 'x.pipeshub.engine.progress':
        this.engineLine(event, event.data as EngineProgressData);
        return;
      case 'workspace.committed':
        this.committed(event.data as WorkspaceCommittedEvent['data']);
        return;
      default:
        return;
    }
  }

  private intent(event: JournalEvent, data: ToolIntentEvent['data']): void {
    const call = this.builder.callForInvocation(data.invocationId);
    if (!isRunCode(call, data.toolId) || this.commands.has(data.invocationId))
      return;
    // A /run's program is the launcher of the line the person typed: its record and live frames carry that line.
    const personRan = this.builder.timeline.runKind === COMMAND_RUN;
    const { code, language } = personRan
      ? { code: '', language: undefined }
      : runCodeArguments(call);
    this.commands.set(
      data.invocationId,
      running(
        data.invocationId,
        'run_code',
        code,
        timestamp(event.ts),
        language,
      ),
    );
  }

  /** A `run_code` call that ended without its record (an older worker): its outcome says how. */
  private outcome(data: Record<string, unknown>): void {
    const invocationId = str(data['invocationId']);
    const command =
      invocationId !== undefined ? this.commands.get(invocationId) : undefined;
    if (command?.status !== 'running') return;
    this.commands.set(command.commandId, {
      ...command,
      status: data['status'] === 'ok' ? 'completed' : 'failed',
    });
  }

  private engineLine(event: JournalEvent, line: EngineProgressData): void {
    const data = record(line.data);
    const callId = str(data['callId']);
    const command =
      callId !== undefined ? this.commands.get(callId) : undefined;
    switch (line.kind) {
      case 'command':
        if (callId !== undefined && command === undefined) {
          this.commands.set(
            callId,
            mayLoseOutput(
              running(callId, 'engine', line.text, timestamp(event.ts)),
              data,
            ),
          );
        }
        return;
      case 'output':
        if (
          this.options.journalOutput !== false &&
          command?.status === 'running'
        ) {
          const stream = data['stream'] === 'stderr' ? 'stderr' : 'stdout';
          this.commands.set(
            command.commandId,
            appended(command, stream, line.text),
          );
        }
        return;
      case 'command_done':
        if (command?.status === 'running') {
          this.commands.set(command.commandId, engineDone(command, data));
        }
        return;
      case 'file':
      case 'diff':
        this.file(line.text, data, line.kind === 'diff');
        return;
      case 'started':
      case 'finished':
      case 'tool':
      case 'helper':
      case 'artifact':
      case 'version':
      case 'approval':
      case 'repository':
      case 'signin':
      case 'tool_result':
      case 'tool_update':
        return;
    }
  }

  /** A commit's paths, each with the kind it names (R27REST-FU-1); one written before kinds, or named none, reads as modified unless known. */
  private committed(data: WorkspaceCommittedEvent['data']): void {
    for (const changed of data.changed) {
      const path = workspacePath(changed);
      if (path === '') continue;
      const kind = data.kinds?.[changed];
      const known = this.files.get(path);
      if (kind === undefined || !FILE_CHANGES.has(kind)) {
        if (known === undefined)
          this.files.set(path, { path, change: 'modified' });
        continue;
      }
      const change =
        known === undefined ? kind : laterChange(known.change, kind);
      if (change === null) this.files.delete(path);
      else this.files.set(path, { ...(known ?? { path }), change });
    }
  }

  private file(
    named: string,
    data: Record<string, unknown>,
    isDiff: boolean,
  ): void {
    const path = workspacePath(named);
    if (path === '') return;
    const change = str(data['change']);
    const known = this.files.get(path);
    const diff = isDiff ? str(data['diff']) : undefined;
    this.files.set(path, {
      path,
      change:
        change !== undefined && FILE_CHANGES.has(change)
          ? (change as FileChange)
          : (known?.change ?? 'modified'),
      ...(diff !== undefined
        ? {
            diff,
            ...(data['truncated'] === true ? { diffTruncated: true } : {}),
          }
        : known?.diff !== undefined
          ? {
              diff: known.diff,
              ...(known.diffTruncated === true ? { diffTruncated: true } : {}),
            }
          : {}),
    });
  }

  view(
    runStatus: RunStatus = 'running',
    followups: Followup[] = [],
  ): ActivityView {
    const { timeline } = this.builder;
    const status =
      timeline.end !== null ? endStatus(timeline.end) : RUN_STATUS[runStatus];
    const ended = TERMINAL.has(status);
    return {
      runId: this.options.runId,
      status,
      engine: timeline.engine !== null,
      ...capped(
        [...this.commands.values()].map((command) =>
          settled(withSkills(command), ended),
        ),
        0,
      ),
      tools: toolsOf(this.builder, ended),
      tasks: timeline.tasks.map(({ taskId, title, status }) => ({
        taskId,
        title,
        status,
      })),
      files: classifiedFiles([...this.files.values()], timeline.artifacts),
      followups,
      ...(timeline.model !== undefined ? { model: timeline.model } : {}),
      ...(timeline.command !== undefined ? { command: timeline.command } : {}),
      ...(timeline.runKind === COMMAND_RUN ? { personRan: true as const } : {}),
      ...(timeline.display?.terminal === false
        ? { outputHidden: true as const }
        : {}),
    };
  }

  /** What the run's pinned spec says the conversation shows (C4.10); undefined until the spec is folded. */
  get display(): Timeline['display'] {
    return this.builder.timeline.display;
  }
}

/**
 * `view` as a run whose pin hides the terminal (C4.10 `ui.terminal`) is sent: every command by name and status,
 * none of its output or its log. Applied to the merged view, so live output never reaches the browser either.
 */
export function withoutHiddenOutput(view: ActivityView): ActivityView {
  if (view.outputHidden !== true) return view;
  return {
    ...view,
    commands: view.commands.map((command) => ({
      ...command,
      stdout: '',
      stderr: '',
      truncated: false,
      liveDropped: 0,
      hasLog: false,
    })),
  };
}

/** The command, marked when the line that started it says its engine can lose its output (R32ENGFU-FU-1). */
function mayLoseOutput(
  command: ActivityCommand,
  data: Record<string, unknown>,
): ActivityCommand {
  return data['outputMayBeLost'] === true
    ? { ...command, outputMayBeLost: true }
    : command;
}

/** An engine's `command_done` line, for a command it journaled no record of. */
function engineDone(
  command: ActivityCommand,
  data: Record<string, unknown>,
): ActivityCommand {
  const exitCode = int(data['exitCode']);
  const status = str(data['status']);
  return {
    ...command,
    status:
      status !== undefined && COMMAND_STATUSES.has(status)
        ? commandStatus(status)
        : exitCode === 0
          ? 'completed'
          : 'failed',
    exitCode,
  };
}

/** `command` naming the skills its text read; the text can arrive after the command did (a live start). */
function withSkills(command: ActivityCommand): ActivityCommand {
  const skills = commandSkills(command.command);
  if (skills.length > 0) return { ...command, skills };
  if (command.skills === undefined) return command;
  const rest = { ...command };
  delete rest.skills;
  return rest;
}

/** Nothing runs after the run ended: a command it never finished was cut off with it. */
const settled = (command: ActivityCommand, ended: boolean): ActivityCommand =>
  ended && command.status === 'running'
    ? { ...command, status: 'cancelled' }
    : command;

export function foldActivity(
  events: readonly JournalEvent[],
  options: ActivityOptions,
): ActivityView {
  const fold = new ActivityFold(options);
  for (const event of events) fold.apply(event);
  return fold.view(options.runStatus, options.followups);
}

export const emptyActivity = (runId: string): ActivityView => ({
  runId,
  status: 'running',
  engine: false,
  commands: [],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [],
  followups: [],
});

function upsert(view: ActivityView, given: ActivityCommand): ActivityView {
  const command = withSkills(given);
  const at = view.commands.findIndex((c) => c.commandId === command.commandId);
  if (at >= 0) {
    const commands = [...view.commands];
    commands[at] = command;
    return { ...view, commands };
  }
  return {
    ...view,
    ...capped([...view.commands, command], view.omittedCommands),
  };
}

/**
 * `view` with one activity-bus frame applied. Only a running command moves:
 * once the journal has a command's record, the record is what it ran.
 */
export function applyLive(
  view: ActivityView,
  frame: ActivityFrame,
): ActivityView {
  const data = frame.data;
  const commandId = str(data['commandId']);
  if (commandId === undefined) return view;
  const known = view.commands.find((c) => c.commandId === commandId);
  if (known !== undefined && known.status !== 'running') return view;
  switch (frame.kind) {
    case 'command_started': {
      const language = str(data['language']) ?? known?.language;
      const startedAt = known?.startedAt ?? int(data['startedAt']);
      const started = mayLoseOutput(
        running(
          commandId,
          data['source'] === 'engine' ? 'engine' : 'run_code',
          known !== undefined && known.command !== ''
            ? known.command
            : (str(data['command']) ?? ''),
          startedAt,
          language,
        ),
        data,
      );
      return upsert(
        view,
        known === undefined
          ? started
          : {
              ...known,
              command: started.command,
              startedAt,
              ...(language !== undefined ? { language } : {}),
              ...(started.outputMayBeLost === true
                ? { outputMayBeLost: true as const }
                : {}),
            },
      );
    }
    case 'command_output': {
      const command = known ?? running(commandId, 'run_code', '', null);
      const stream = data['stream'] === 'stderr' ? 'stderr' : 'stdout';
      const text = str(data['text']);
      if (text !== undefined)
        return upsert(view, appended(command, stream, text));
      const dropped = int(data['dropped']);
      return dropped !== null && dropped > 0
        ? upsert(view, {
            ...command,
            liveDropped: command.liveDropped + dropped,
          })
        : view;
    }
    case 'command_finished': {
      const command = known ?? running(commandId, 'run_code', '', null);
      return upsert(view, {
        ...command,
        status: commandStatus(data['status']),
        exitCode: int(data['exitCode']),
        durationMs: int(data['durationMs']),
      });
    }
    case 'followups':
      return view;
  }
}

/**
 * The journal's view with what the activity bus said of commands the journal
 * has not finished: their live output and, when the bus saw it first, their
 * end. A command only the bus knows (its record not yet committed) is added.
 */
export function mergeLive(
  journal: ActivityView,
  live: ActivityView,
): ActivityView {
  const ended = TERMINAL.has(journal.status);
  let view = journal;
  for (const seen of live.commands) {
    const known = view.commands.find((c) => c.commandId === seen.commandId);
    if (known !== undefined && known.status !== 'running') continue;
    const hasOutput =
      seen.stdout !== '' || seen.stderr !== '' || seen.truncated;
    const language = known?.language ?? seen.language;
    const merged: ActivityCommand =
      known === undefined
        ? seen
        : {
            ...known,
            command: known.command !== '' ? known.command : seen.command,
            ...(language !== undefined ? { language } : {}),
            status: seen.status,
            exitCode: seen.exitCode ?? known.exitCode,
            startedAt: known.startedAt ?? seen.startedAt,
            durationMs: seen.durationMs ?? known.durationMs,
            ...(hasOutput
              ? {
                  stdout: seen.stdout,
                  stderr: seen.stderr,
                  truncated: seen.truncated,
                }
              : {}),
            liveDropped: Math.max(known.liveDropped, seen.liveDropped),
            ...(known.outputMayBeLost === true || seen.outputMayBeLost === true
              ? { outputMayBeLost: true as const }
              : {}),
          };
    view = upsert(view, settled(merged, ended));
  }
  return view;
}
