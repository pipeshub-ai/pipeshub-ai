import type { ApprovalPartStatus, RunTimelineView, SSEEngineProgressEvent } from '@/chat/types';
import { engineSteps, type EngineStep } from '@/chat/engine-steps';
import { jobStatusOf, type JobStatus } from './job-status';
import { proseBlocks } from './durable-trail';
import { askQuestion, askStamp } from './approval-words';
import { commandTitle } from './command-titles';
import { plainDetail, toolLabel, toolTitle } from './step-titles';
import type { SandboxAgent, SandboxStep, SandboxWorker, WorkerState } from './view-models';
import type { Words } from './words';

/**
 * A child engine run (the job a `code_task` hands to Claude Code, Codex, OpenCode and the rest) as the design's
 * `SandboxWorker` and `WorkerState` (design.md §6B.42), plus its status word.
 *
 * Steps come from the engine's progress frames through `engineSteps` (the chat's own reading of them, CTL-4), so the
 * job panel and the classic timeline cannot disagree about what ran. An approval the engine raised is a parked
 * `approval` step at the point it asked; answered, it records the answer; unanswered, the job Needs you. Silence is
 * never a yes: an ask that expired is recorded as "no".
 */

export interface EngineRunInput {
  runId: string;
  /** What the job was asked to do: the parent's `codeTaskGoal`, or the engine's own `started` frame. */
  goal?: string | null;
  /** The engine kind (`claude_code`, `codex`, `opencode`…), from the timeline's `executor` or `codingEngine`. */
  engine?: string | null;
  /** The run's status: the timeline's, or the parent's child view. */
  status: string;
  frames: ReadonlyArray<SSEEngineProgressEvent>;
  /** Approvals on this run, as the timeline lists them. */
  approvals?: ReadonlyArray<{ interactionId?: string; toolName?: string; displayName?: string; argsSummary?: string | null; status: ApprovalPartStatus | string }>;
  startedAt?: number;
  finishedAt?: number;
}

export interface EngineJob {
  worker: SandboxWorker;
  state: WorkerState;
  status: JobStatus;
}

const ENGINE_NAMES: Readonly<Record<string, SandboxAgent>> = {
  claude_code: 'Claude Code',
  'claude-code': 'Claude Code',
  claudecode: 'Claude Code',
  codex: 'Codex',
  opencode: 'OpenCode',
  gemini: 'Gemini',
  gemini_cli: 'Gemini',
  goose: 'Goose',
  qwen: 'Qwen',
  qwen_code: 'Qwen',
  deepseek: 'DeepSeek',
  dsh: 'DeepSeek',
};

/** The engine's product name: an ACP agent (`acp-gemini`) is named by its agent. */
export function agentName(engine: string | null | undefined): SandboxAgent {
  const kind = (engine ?? '').toLowerCase().replace(/^acp-/, '');
  return (kind && ENGINE_NAMES[kind]) || 'Background agent';
}

const TITLE_MAX = 80;

/**
 * The job's name wherever it shows (its card, report, the floating ask, the chat's list, Jobs): the goal's first line
 * as it was asked, cut on a word past 80 characters. One name for one job: a four-word cut here once named the job
 * "File the quarter ticket..." over the composer while its card said "File the quarter ticket c4bcf4".
 */
export function jobTitle(goal: string | null | undefined, t: Words): string {
  const line = (goal ?? '').trim().split('\n')[0]?.trim().replace(/\s+/g, ' ') ?? '';
  if (!line) return t('ui.job.untitled');
  if (line.length <= TITLE_MAX) return line;
  const cut = line.slice(0, TITLE_MAX + 1);
  const end = cut.lastIndexOf(' ');
  return `${(end > TITLE_MAX / 2 ? cut.slice(0, end) : line.slice(0, TITLE_MAX)).replace(/[.,;:!?]+$/, '')}…`;
}

function toSandboxStep(step: EngineStep, t: Words): SandboxStep {
  switch (step.kind) {
    case 'command': {
      const facts = { command: step.command, status: step.status, exitCode: step.exitCode, output: step.output };
      const done = commandTitle({ ...facts, status: step.status === 'running' ? 'completed' : step.status }, t);
      return {
        id: step.callId,
        kind: 'command',
        title: done.title,
        activeTitle: commandTitle({ ...facts, status: 'running' }, t).title,
        ...(step.status !== 'running' && done.result ? { result: done.result } : {}),
        command: step.command,
        ...(step.output ? { output: step.output } : {}),
        // A test run's result already says it failed; any other command says how it stopped.
        ...(step.status === 'failed' && !done.result ? { error: t('ui.job.commandFailed', { code: step.exitCode ?? '' }) } : {}),
        ms: 0,
      };
    }
    case 'file':
      return { id: step.callId, kind: 'file', title: t(`ui.trail.${step.file.change}File`, { path: step.file.path }), file: step.file.path, ms: 0 };
    case 'helper':
      return { id: step.callId, kind: 'message', title: step.task ? t('ui.trail.helperTask', { task: step.task }) : t('ui.trail.helper'), ms: 0 };
    default: {
      const { title, detail } = toolTitle({ toolName: step.toolName, displayName: step.displayName, argsSummary: step.argsSummary, step: step.step }, t);
      const browser = step.step?.action === 'readPage' || step.step?.action === 'webSearch';
      return {
        id: step.callId,
        kind: browser ? 'browser' : 'command',
        title,
        ...(detail ? { output: detail } : {}),
        ...(browser && step.step?.href ? { url: step.step.href } : {}),
        ms: 0,
      };
    }
  }
}

const isDone = (step: EngineStep): boolean => step.kind === 'file' || step.status !== 'running';

export function engineJob(input: EngineRunInput, t: Words): EngineJob {
  const ended = !['running', 'queued', 'suspended'].includes(input.status);
  const frames = [...input.frames];
  const all = engineSteps(frames, ended);

  // Each approval frame is placed after the steps that came before it.
  const asks: Array<{ at: number; interactionId: string; toolName: string }> = [];
  frames.forEach((frame, i) => {
    if (frame.kind !== 'approval') return;
    const data = frame.data ?? {};
    asks.push({
      at: engineSteps(frames.slice(0, i), false).length,
      interactionId: typeof data.interactionId === 'string' ? data.interactionId : `ask-${i}`,
      toolName: typeof data.toolName === 'string' ? data.toolName : frame.text,
    });
  });
  for (const a of input.approvals ?? []) {
    if (a.interactionId && !asks.some((x) => x.interactionId === a.interactionId)) {
      asks.push({ at: all.length, interactionId: a.interactionId, toolName: a.toolName ?? 'tool' });
    }
  }

  const steps: SandboxStep[] = [];
  const answers: Record<string, 'yes' | 'no'> = {};
  let firstOpen = -1;
  let askIndex = 0;
  const sorted = [...asks].sort((x, y) => x.at - y.at);
  const pushAsks = (upTo: number) => {
    while (askIndex < sorted.length && sorted[askIndex].at <= upTo) {
      const ask = sorted[askIndex++];
      const known = input.approvals?.find((a) => a.interactionId === ask.interactionId);
      const label = toolLabel(known?.toolName ?? ask.toolName, known?.displayName);
      steps.push({
        id: ask.interactionId,
        kind: 'approval',
        title: askQuestion(t, { toolName: known?.toolName ?? ask.toolName, displayName: known?.displayName }),
        question: t('ui.job.allowQuestion', { action: label }),
        approve: t('ui.job.allow'),
        decline: t('ui.job.dontAllow'),
        ...(plainDetail(known?.argsSummary) ? { output: plainDetail(known?.argsSummary) } : {}),
        ms: 0,
      });
      const status = known?.status;
      const call = { toolName: known?.toolName ?? ask.toolName, displayName: known?.displayName };
      const parked = steps[steps.length - 1];
      if (status === 'approved') answers[ask.interactionId] = 'yes';
      else if (status === 'denied' || status === 'expired' || status === 'closed') answers[ask.interactionId] = 'no';
      else if (status === 'pending' || (!status && !ended)) {
        if (firstOpen < 0) firstOpen = steps.length - 1;
      } else if (ended) answers[ask.interactionId] = 'no';
      if (answers[ask.interactionId]) parked.stamp = askStamp(t, status === 'approved' || status === 'denied' || status === 'expired' ? status : 'closed', call);
    }
  };
  const asked = new Set(asks.map((a) => a.interactionId));
  all.forEach((step, i) => {
    pushAsks(i);
    // A call that waited for an ask is that ask's row (FIX-TOOLWORDS), never a second step left running beside it.
    if (step.kind === 'tool' && step.interactionId && asked.has(step.interactionId)) return;
    steps.push(toSandboxStep(step, t));
    if (firstOpen < 0 && !isDone(step)) firstOpen = steps.length - 1;
  });
  pushAsks(Number.MAX_SAFE_INTEGER);

  const openAsk = steps.some((s) => s.kind === 'approval' && !answers[s.id] && !ended);
  const status = jobStatusOf(input.status, { openAsk });
  const goal = input.goal ?? frames.find((f) => f.kind === 'started')?.text ?? null;
  const finished = [...frames].reverse().find((f) => f.kind === 'finished');
  const outputs = frames
    .filter((f) => f.kind === 'artifact')
    .reduce<Array<{ name: string; artifactId?: string }>>((list, f) => {
      const artifactId = typeof f.data?.artifactId === 'string' ? f.data.artifactId : undefined;
      if (!list.some((o) => (artifactId ? o.artifactId === artifactId : o.name === f.text))) list.push({ name: f.text, ...(artifactId ? { artifactId } : {}) });
      return list;
    }, []);

  const worker: SandboxWorker = {
    id: input.runId,
    title: jobTitle(goal, t),
    agent: agentName(input.engine),
    ...(goal ? { brief: { task: goal, sent: [], withheld: [], reach: '' } } : {}),
    steps,
    outputs,
    report: finished?.text ? proseBlocks(finished.text) : [],
  };
  const state: WorkerState = {
    done: status === 'upNext' ? 0 : firstOpen >= 0 && !ended ? firstOpen : steps.length,
    startedAt: input.startedAt ?? 0,
    ...(input.finishedAt ? { finishedAt: input.finishedAt } : {}),
    ...(status === 'stopped' ? { stopped: true } : {}),
    ...(status === 'couldntFinish' ? { failed: true } : {}),
    ...(status === 'upNext' ? { queued: true } : {}),
    notes: [],
    answers,
    skipped: [],
  };
  return { worker, state, status };
}

/** The same, from a child run's timeline (`GET …/runs/:runId/timeline`). */
export function engineJobFromTimeline(timeline: RunTimelineView, t: Words, goal?: string | null): EngineJob {
  return engineJob(
    {
      runId: timeline.runId,
      goal,
      engine: timeline.codingEngine ?? timeline.executor ?? null,
      status: timeline.status,
      frames: timeline.engine?.frames ?? [],
      approvals: timeline.approvals.map((a) => ({
        interactionId: a.interactionId,
        toolName: a.toolName,
        displayName: a.displayName,
        argsSummary: a.argsSummary,
        status: a.status,
      })),
    },
    t,
  );
}
