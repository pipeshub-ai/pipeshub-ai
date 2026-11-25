import type { TFunction } from 'i18next';
import { budgetRefusalKey } from '../../../code-tasks';
import { runFailureWords } from '../../../run-failure-words';
import { describeTool, isAskUserQuestionTool } from '../../../utils/tool-display';
import type { MessagePart, SSEEngineProgressEvent } from '../../../types';
import { jobClock } from '@/lib/harness-jobs/jobs';
import type { JobAsk, JobStatus, JobStreamState } from './data';

/**
 * What a job card says (CLASSIC-JOBS D2, D11, D12): one of the six status words, and the second half of its status
 * line (what it does now, what it asks, how it ended). Pure, from the parent's `sub_agent` part, the job's own
 * stream and the one timeline read after it ended.
 */

export interface JobCardInput {
  part: Pick<MessagePart, 'status' | 'errorType' | 'parts' | 'memoryLimitMb'>;
  stream?: JobStreamState;
  /** The chat has an ask of this job open under its row. */
  waiting?: boolean;
  /** G11: an admin's stop, from the timeline read once it ended. */
  operatorStop?: { reason: string } | null;
  /** The viewer started the chat: only then is a plain stop "by you". */
  isOwner: boolean;
}

export type StopCause = 'you' | 'admin' | 'budget' | 'time' | 'other';

export interface JobCardState {
  status: JobStatus;
  stopCause?: StopCause;
  /** The run's own failure code, for its words. */
  failure?: string;
  /** With `failure` `out_of_memory`: the limit it met, in MB, so the words name it as the answer's do. */
  memoryLimitMb?: number;
  ask?: JobAsk;
}

const TIME_LIMITS: ReadonlySet<string> = new Set(['deadline_exceeded', 'max_turns_exceeded', 'time', 'turns']);

/**
 * The run's status as the stream or the parent's part says it: the part is the authority on "it ended", the job's
 * own stream on whether it has started. A running part says only that the job was handed out, so before the job's
 * stream has said anything a job with no step yet reads Starting, never Working first and Starting after (F6); one
 * with steps has started, whatever a late queued snapshot says.
 */
function runStatus(input: JobCardInput): string {
  const { part, stream } = input;
  if (stream?.ended) return stream.status;
  if (part.status === 'failed') return part.errorType === 'cancelled' ? 'cancelled' : 'failed';
  if (part.status === 'completed') return 'completed';
  const started = (part.parts?.length ?? 0) > 0;
  if (!stream) return started ? 'running' : 'queued';
  return stream.status === 'queued' && started ? 'running' : stream.status;
}

export function jobCardState(input: JobCardInput): JobCardState {
  const code = input.part.errorType ?? input.stream?.errorCode;
  const status = runStatus(input);
  if (input.operatorStop || code === 'operator_stopped') return { status: 'stopped', stopCause: 'admin' };
  if (status === 'cancelled') return { status: 'stopped', stopCause: input.isOwner ? 'you' : 'other' };
  if (status === 'failed') {
    if (budgetRefusalKey(code) !== null) return { status: 'stopped', stopCause: 'budget' };
    if (code && TIME_LIMITS.has(code)) return { status: 'stopped', stopCause: 'time' };
    const limit = input.part.memoryLimitMb ?? input.stream?.memoryLimitMb;
    return {
      status: 'couldntFinish',
      ...(code ? { failure: code } : {}),
      ...(code === 'out_of_memory' && limit !== undefined ? { memoryLimitMb: limit } : {}),
    };
  }
  if (status === 'completed') return { status: 'done' };
  const ask = input.stream?.asks.find((a) => a.status === 'pending');
  if (ask || input.waiting) return { status: 'needsYou', ...(ask ? { ask } : {}) };
  if (status === 'queued') return { status: 'upNext' };
  return { status: 'working' };
}

const STATUS_KEYS: Record<JobStatus, string> = {
  upNext: 'jobCard.status.upNext',
  working: 'jobCard.status.working',
  needsYou: 'jobCard.status.needsYou',
  done: 'jobCard.status.done',
  couldntFinish: 'jobCard.status.couldntFinish',
  stopped: 'jobCard.status.stopped',
};

const STOP_KEYS: Record<StopCause, string> = {
  you: 'jobCard.stopped.byYou',
  admin: 'jobCard.stopped.byAdmin',
  budget: 'jobCard.stopped.budget',
  time: 'jobCard.stopped.time',
  other: 'jobCard.status.stopped',
};

const ADMIN_REASONS = ['security', 'cost', 'incident', 'maintenance', 'other'] as const;

/** The status word; a stop says who or what stopped it (D12), never a code. */
export function statusWord(state: JobCardState, operatorStop: { reason: string } | null | undefined, t: TFunction): string {
  if (state.status !== 'stopped' || !state.stopCause) return t(STATUS_KEYS[state.status]);
  if (state.stopCause === 'admin') {
    const reason = (ADMIN_REASONS as readonly string[]).includes(operatorStop?.reason ?? '') ? operatorStop?.reason : 'other';
    return t(STOP_KEYS.admin, { reason: t(`jobCard.adminReason.${reason}`) });
  }
  return t(STOP_KEYS[state.stopCause]);
}

/** "Made report.pdf", "Changed 3 files", "Finished": what a done job left, from its frames. */
export function doneLine(frames: ReadonlyArray<SSEEngineProgressEvent>, t: TFunction): string {
  const made = frames.filter((f) => f.kind === 'artifact' && f.text && (f.data as Record<string, unknown> | undefined)?.lifecycle !== 'discarded');
  const latest = made[made.length - 1];
  if (latest) return t('jobCard.made', { file: latest.text.slice(latest.text.lastIndexOf('/') + 1) });
  const files = new Set(frames.filter((f) => (f.kind === 'file' || f.kind === 'diff') && f.text).map((f) => f.text));
  if (files.size > 0) return t('jobCard.changedFiles', { count: files.size });
  return t('jobCard.finished');
}

/** What a job waiting on the person asks, in words: a question, or the tool it wants to use. */
export function askLine(ask: JobAsk | undefined, t: TFunction): string {
  if (!ask || isAskUserQuestionTool(ask.toolName)) return t('jobCard.hasQuestion');
  return t('jobCard.wantsTo', { tool: describeTool(ask.toolName, ask.displayName).displayName });
}

/** The status line's second half; null when the word says it all. */
export function secondLine(
  state: JobCardState,
  facts: { now: string | null; frames: ReadonlyArray<SSEEngineProgressEvent> },
  t: TFunction,
): string | null {
  switch (state.status) {
    case 'upNext':
      return t('jobCard.booting');
    case 'working':
      return facts.now;
    case 'needsYou':
      return askLine(state.ask, t);
    case 'done':
      return doneLine(facts.frames, t);
    case 'couldntFinish':
      return runFailureWords(
        state.failure ?? null,
        null,
        state.memoryLimitMb !== undefined ? { details: { memoryLimitMb: state.memoryLimitMb } } : {},
      );
    default:
      return null;
  }
}

/** How long it has run, or ran: from the stream's own clock, else the saved part's. */
export function elapsedMs(stream: JobStreamState | undefined, part: Pick<MessagePart, 'durationMs'>, now: number): number | null {
  if (stream?.startedAt !== undefined) return Math.max(0, (stream.finishedAt ?? now) - stream.startedAt);
  return typeof part.durationMs === 'number' && Number.isFinite(part.durationMs) ? part.durationMs : null;
}

/** A job's elapsed time on its card and report: the one job clock (`lib/harness-jobs` `jobClock`). */
export function clockWords(ms: number, t: TFunction): string {
  return jobClock(ms, (key, vars) => t(key, vars) as string);
}
