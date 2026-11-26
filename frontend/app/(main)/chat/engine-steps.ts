import { plainTerminalText } from './code-tasks';
import { skillNames } from './utils/tool-display';
import { copiedRepository, isOpenPullRequest, pullRequestActivity, pushActivity, type RepoActivity } from './repository-steps';
import type { ChatArtifact, CodeTaskFile, MessagePart, SSEEngineProgressEvent, ToolStepAction, ToolStepFacts } from './types';

/**
 * A coder (engine) run's work as the answer's timeline shows it (CTL-4), read from its progress frames, live or
 * saved: its commands and tool calls in order, and the files it changed. The full output and every version stay in
 * "View activity".
 */
export interface EngineCommandStep {
  kind: 'command';
  callId: string;
  command: string;
  status: 'running' | 'completed' | 'failed';
  exitCode?: number;
  /** What the command printed, as the frames carried it (a saved answer may keep only part of it). */
  output: string;
  /** The skills it read, as the server named them (FIX-SKILLSHOW). */
  skills?: string[];
}

export interface EngineToolStep {
  kind: 'tool';
  callId: string;
  toolName: string;
  displayName?: string;
  status: 'running' | 'completed' | 'failed';
  /** What the tool returned (bounded by the worker): a gateway tool's, or one of the engine's own. */
  result?: string;
  /** The end the engine or the gateway reported; without it, a step is guessed done by what follows. */
  ended?: boolean;
  /** The call's arguments, for "Details" only (WP-112). */
  args?: string;
  argsSummary?: string;
  resultSummary?: string;
  /** The tool's own view of its result (a search's hits, linked); unchecked here, see `toolResultView`. */
  resultView?: unknown;
  /** Which page, query, pattern or file, and what came of it (WP-112). */
  step?: ToolStepFacts;
  /** The skills the call read, as the server named them (FIX-SKILLSHOW). */
  skills?: string[];
  /** The approval this call waited for (FIX-TOOLWORDS): the ask is this step, not a second one. */
  interactionId?: string;
}

/** One file the run created, changed or deleted, where it first touched it; its diff once the run saved it. */
export interface EngineFileStep {
  kind: 'file';
  callId: string;
  file: CodeTaskFile;
}

/**
 * One of the engine's own helpers (Claude Code's Agent tool, a Codex sub-agent, an opencode task, EV-07) and the
 * steps it took, which nest under it as a sub-agent's do.
 */
export interface EngineHelperStep {
  kind: 'helper';
  callId: string;
  subSessionId: string;
  /** What the run's agent asked it to do, in the agent's words; none when the engine did not say. */
  task?: string;
  status: 'running' | 'completed' | 'failed';
  steps: EngineStep[];
}

export type EngineStep = EngineCommandStep | EngineToolStep | EngineFileStep | EngineHelperStep;

/** One file a run made or changed, as the "Files changed" block lists it for engine and loop runs alike. */
export interface ChangedFile {
  path: string;
  change: 'added' | 'modified' | 'deleted';
  sizeBytes?: number;
  /** A unified diff when the run has one (an engine's workspace); none for a file a loop run made. */
  diff?: string;
  diffTruncated?: boolean;
  binary?: boolean;
  tooLarge?: boolean;
  additions: number;
  deletions: number;
  /** The artifact the file was published as, by record id: its card and preview. */
  artifactId?: string;
}

const str = (value: unknown): string | undefined => (typeof value === 'string' ? value : undefined);
const num = (value: unknown): number | undefined => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);

const FILE_CHANGES: ReadonlySet<string> = new Set(['added', 'modified', 'deleted']);

const STEP_ACTIONS: ReadonlySet<string> = new Set<ToolStepAction>(['readPage', 'webSearch', 'findFiles', 'searchFiles', 'readFile', 'listFiles']);

/** A frame's `step`, checked: an action this chat names, its target, a link only when it is http(s). */
export function stepFacts(value: unknown): ToolStepFacts | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  const action = str(raw['action']);
  if (!action || !STEP_ACTIONS.has(action)) return undefined;
  const href = str(raw['href']);
  const count = num(raw['count']);
  const sources = Array.isArray(raw['sources']) ? raw['sources'].filter((s): s is string => typeof s === 'string') : [];
  return {
    action: action as ToolStepAction,
    ...(str(raw['target']) ? { target: str(raw['target']) } : {}),
    ...(href && /^https?:\/\//i.test(href) ? { href } : {}),
    ...(str(raw['title']) ? { title: str(raw['title']) } : {}),
    ...(count !== undefined && count >= 0 ? { count } : {}),
    ...(sources.length > 0 ? { sources } : {}),
  };
}

/** A step's facts with a later frame's: what the call asked stays, what came back is added. */
function mergedStep(known: ToolStepFacts | undefined, next: ToolStepFacts | undefined): ToolStepFacts | undefined {
  if (!next) return known;
  if (!known) return next;
  return { ...known, ...next, target: known.target ?? next.target, href: known.href ?? next.href };
}

/** The fields of a `tool` or `tool_result` frame a step shows (WP-112). */
function withFacts(step: EngineToolStep, data: Record<string, unknown>): void {
  const args = str(data['args']);
  if (args !== undefined) step.args = args;
  const argsSummary = str(data['argsSummary']);
  if (argsSummary) step.argsSummary = argsSummary;
  const resultSummary = str(data['resultSummary']);
  if (resultSummary) step.resultSummary = resultSummary;
  if (data['resultView'] !== undefined) step.resultView = data['resultView'];
  const facts = mergedStep(step.step, stepFacts(data['step']));
  if (facts) step.step = facts;
  const skills = skillNames(data['skills']);
  if (skills) step.skills = skills;
}

/**
 * The run's steps in order, a helper's under it; `ended`: a step still open when the run ended is closed, not left
 * running.
 */
export function engineSteps(frames: readonly SSEEngineProgressEvent[], ended: boolean): EngineStep[] {
  const steps: EngineStep[] = [];
  const byCall = new Map<string, EngineStep>();
  const byPath = new Map<string, EngineFileStep>();
  const helpers = new Map<string, EngineHelperStep>();
  const asked = new Map<string, string>();
  for (const frame of frames) {
    const data = frame.data ?? {};
    const helper = helpers.get(str(data['subSessionId']) ?? '');
    // A helper's start and end are its own row; every other frame of a helper's step goes under it.
    const into = helper && frame.kind !== 'helper' ? helper.steps : steps;
    const callId = str(data['callId']) ?? `${frame.kind}-${steps.length}`;
    switch (frame.kind) {
      case 'helper': {
        const subSessionId = str(data['subSessionId']);
        if (!subSessionId) break;
        if (helper) {
          if (data['status'] === 'completed' || data['status'] === 'failed') helper.status = data['status'];
          break;
        }
        const task = str(data['task'])?.trim();
        const step: EngineHelperStep = { kind: 'helper', callId, subSessionId, status: 'running', steps: [], ...(task ? { task } : {}) };
        steps.push(step);
        helpers.set(subSessionId, step);
        break;
      }
      case 'tool_result': {
        const step = byCall.get(callId);
        if (step?.kind !== 'tool') break;
        step.ended = true;
        step.status = data['status'] === 'failed' ? 'failed' : 'completed';
        const result = str(data['result']);
        if (result !== undefined) step.result = result;
        withFacts(step, data);
        break;
      }
      case 'file':
      case 'diff': {
        if (!frame.text) break;
        const change = str(data['change']);
        const known = byPath.get(frame.text);
        const diff = str(data['diff']);
        const file: CodeTaskFile = {
          ...known?.file,
          path: frame.text,
          // The saved diff knows what the workspace holds; the engine's own word only until then.
          change:
            change && FILE_CHANGES.has(change) && (frame.kind === 'diff' || known === undefined)
              ? (change as CodeTaskFile['change'])
              : (known?.file.change ?? 'modified'),
          ...(diff !== undefined ? { diff } : {}),
          ...(data['truncated'] === true && frame.kind === 'diff' ? { diffTruncated: true } : {}),
          ...(data['binary'] === true ? { binary: true } : {}),
          ...(data['tooLarge'] === true ? { tooLarge: true } : {}),
          ...(num(data['sizeBytes']) !== undefined ? { sizeBytes: num(data['sizeBytes']) } : {}),
        };
        if (known) known.file = file;
        else {
          const step: EngineFileStep = { kind: 'file', callId: `file:${frame.text}`, file };
          into.push(step);
          byPath.set(frame.text, step);
        }
        break;
      }
      case 'command': {
        const skills = skillNames(data['skills']);
        const step: EngineCommandStep = { kind: 'command', callId, command: frame.text, status: 'running', output: '', ...(skills ? { skills } : {}) };
        into.push(step);
        byCall.set(callId, step);
        break;
      }
      case 'output': {
        const step = byCall.get(callId);
        if (step?.kind === 'command') step.output += plainTerminalText(frame.text);
        break;
      }
      case 'command_done': {
        const step = byCall.get(callId);
        if (step?.kind !== 'command') break;
        const exitCode = num(data['exitCode']);
        step.status = exitCode === undefined || exitCode === 0 ? 'completed' : 'failed';
        if (exitCode !== undefined) step.exitCode = exitCode;
        break;
      }
      case 'approval': {
        const step = byCall.get(callId);
        const interactionId = str(data['interactionId']);
        if (!interactionId) break;
        // The worker can journal the ask before the engine's line for the call it gates.
        if (step?.kind === 'tool') step.interactionId = interactionId;
        else asked.set(callId, interactionId);
        break;
      }
      case 'tool':
      case 'tool_update': {
        // An engine that sends a call's input after announcing it sends a `tool_update` (WP-112): the same step gains
        // it; an update whose start was left out of a bounded journal starts the step.
        const known = byCall.get(callId);
        const step: EngineToolStep = known?.kind === 'tool' ? known : { kind: 'tool', callId, toolName: frame.text, status: 'running' };
        const display = str(data['displayName']);
        if (display) step.displayName = display;
        withFacts(step, data);
        if (known?.kind !== 'tool') {
          // An ask journaled before this line names no call id; the worker then names the ask on the line instead.
          const interactionId = asked.get(callId) ?? str(data['interactionId']);
          if (interactionId) step.interactionId = interactionId;
          into.push(step);
          byCall.set(callId, step);
        }
        break;
      }
      default:
        break;
    }
  }
  settle(steps, ended);
  return steps;
}

/** A journal from before tool ends were reported: one followed by anything else, or a finished run, has returned. */
function settle(steps: EngineStep[], ended: boolean): void {
  steps.forEach((step, index) => {
    if (step.kind === 'tool' && !step.ended && (ended || index < steps.length - 1)) step.status = 'completed';
    if (step.kind === 'command' && step.status === 'running' && ended) step.status = 'completed';
    if (step.kind === 'helper') {
      settle(step.steps, ended || step.status !== 'running');
      if (step.status === 'running' && ended) step.status = 'completed';
    }
  });
}

const CHANGES: ReadonlySet<string> = new Set(['added', 'modified', 'deleted']);

/** The lines a unified diff adds and removes, headers left out. */
function counts(diff: string | undefined): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const line of (diff ?? '').split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---')) continue;
    if (line.startsWith('+')) additions += 1;
    else if (line.startsWith('-')) deletions += 1;
  }
  return { additions, deletions };
}

const basename = (path: string): string => path.slice(path.lastIndexOf('/') + 1);

/** The files an engine run changed (its `diff` and `file` frames), each with the artifact it was published as. */
export function changedFilesFromEngine(frames: readonly SSEEngineProgressEvent[]): ChangedFile[] {
  const files = new Map<string, ChangedFile>();
  const published = new Map<string, string>();
  for (const frame of frames) {
    const data = frame.data ?? {};
    if (frame.kind === 'artifact') {
      const artifactId = str(data['artifactId']);
      if (artifactId && data['lifecycle'] !== 'discarded') published.set(frame.text, artifactId);
      continue;
    }
    if (frame.kind !== 'diff' && frame.kind !== 'file') continue;
    const change = str(data['change']);
    const diff = str(data['diff']);
    const known = files.get(frame.text);
    files.set(frame.text, {
      ...known,
      path: frame.text,
      change: change && CHANGES.has(change) ? (change as ChangedFile['change']) : (known?.change ?? 'modified'),
      ...(num(data['sizeBytes']) !== undefined ? { sizeBytes: num(data['sizeBytes']) } : {}),
      ...(diff !== undefined ? { diff } : {}),
      ...(data['truncated'] === true ? { diffTruncated: true } : {}),
      ...(data['binary'] === true ? { binary: true } : {}),
      ...(data['tooLarge'] === true ? { tooLarge: true } : {}),
      ...counts(diff ?? known?.diff),
    });
  }
  return [...files.values()].map((file) => {
    const artifactId = published.get(basename(file.path));
    return artifactId ? { ...file, artifactId } : file;
  });
}

/** The files a loop run made through run_code or the sandbox: its artifacts, a new version counted as a change. */
export function changedFilesFromArtifacts(artifacts: readonly ChatArtifact[]): ChangedFile[] {
  return artifacts
    .filter((artifact) => !artifact.unavailable && artifact.visibility !== 'STAGING')
    .map((artifact) => ({
      path: artifact.fileName,
      change: (artifact.version ?? 1) > 1 ? ('modified' as const) : ('added' as const),
      ...(artifact.sizeBytes > 0 ? { sizeBytes: artifact.sizeBytes } : {}),
      additions: 0,
      deletions: 0,
      ...(artifact.recordId ? { artifactId: artifact.recordId } : {}),
    }));
}

/**
 * An engine run's `engine_task` part as the timeline rows a loop run would have (CTL-4): each command an
 * `engine_command` row, each gateway tool a `tool_call` row, and its repository work (the copy, each ended `git push`,
 * an opened pull request) an `engine_repo` row; `ended`: nothing it started is still running.
 */
export function withEngineSteps(parts: MessagePart[], ended: boolean): MessagePart[] {
  if (!parts.some((part) => part.type === 'engine_task')) return parts;
  return parts.flatMap((part): MessagePart[] => {
    if (part.type !== 'engine_task') return [part];
    const run = part.runId !== undefined ? { runId: part.runId } : {};
    const frames = part.frames ?? [];
    // The repository PipesHub copied before the engine started (WP-26): its first row, and the branch a push names.
    const copied = copiedRepository(frames);
    const rows = engineSteps(frames, ended).map((step) => stepPart(step, { run, copied }));
    return copied ? [repoRow('repository:copied', copied, 'completed', run), ...rows] : rows;
  });
}

function repoRow(callId: string, repo: RepoActivity, status: 'completed' | 'failed', run: { runId?: string }): MessagePart {
  return { type: 'engine_repo', toolCallId: callId, repo, status, ...run };
}

interface StepContext {
  run: { runId?: string };
  /** The repository PipesHub copied before the engine started (WP-26), so a push or pull request row names it. */
  copied: ReturnType<typeof copiedRepository>;
}

/** One engine step as a timeline row; a helper is a sub-agent row (not a run of its own) holding its steps (EV-07). */
function stepPart(step: EngineStep, ctx: StepContext): MessagePart {
  const { run, copied } = ctx;
  switch (step.kind) {
    case 'command': {
      const push = step.status === 'running' ? null : pushActivity(step.command, step.status === 'failed', copied?.branch ?? null);
      if (push) return repoRow(step.callId, push, step.status === 'failed' ? 'failed' : 'completed', run);
      return {
        type: 'engine_command',
        toolCallId: step.callId,
        command: step.command,
        content: step.output,
        status: step.status,
        ...(step.exitCode !== undefined ? { exitCode: step.exitCode } : {}),
        ...(step.skills ? { skills: step.skills } : {}),
        ...run,
      };
    }
    case 'file':
      return { type: 'engine_file', toolCallId: step.callId, file: step.file, status: 'completed', ...run };
    case 'helper':
      return {
        type: 'sub_agent',
        toolCallId: step.callId,
        status: step.status,
        engineHelper: step.task !== undefined ? { task: step.task } : {},
        parts: step.steps.map((nested) => stepPart(nested, ctx)),
      };
    default:
      if (step.status !== 'running' && isOpenPullRequest(step.toolName)) {
        const pr = pullRequestActivity(step.result, step.status === 'failed', copied?.repository ?? null);
        return repoRow(step.callId, pr, pr.action === 'prFailed' ? 'failed' : 'completed', run);
      }
      return {
        type: 'tool_call',
        toolCallId: step.callId,
        toolName: step.toolName,
        status: step.status,
        ...(step.displayName ? { displayName: step.displayName } : {}),
        ...(step.result !== undefined ? { resultPreview: step.result } : {}),
        ...(step.args !== undefined ? { args: step.args } : {}),
        ...(step.argsSummary ? { argsSummary: step.argsSummary } : {}),
        ...(step.resultSummary ? { resultSummary: step.resultSummary } : {}),
        ...(step.resultView !== undefined ? { resultView: step.resultView } : {}),
        ...(step.step ? { step: step.step } : {}),
        ...(step.skills ? { skills: step.skills } : {}),
        ...(step.interactionId ? { interactionId: step.interactionId } : {}),
        ...run,
      };
  }
}

/** Rows with every engine helper's steps in its place instead of the helper: for views that list steps flat. */
export function withoutHelpers(parts: MessagePart[]): MessagePart[] {
  return parts.flatMap((part) => (part.type === 'sub_agent' && part.engineHelper ? withoutHelpers(part.parts ?? []) : [part]));
}
