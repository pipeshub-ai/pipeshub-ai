import type { MessagePart, PendingApproval } from '@/chat/types';
import { engineJob, type EngineJob } from './engine-worker';
import type { JobStatus } from './job-status';
import type { JobStreamState } from './job-stream';
import type { SandboxAgent, SandboxStep, SandboxWorker, WorkerState, AnswerBlock } from './view-models';
import type { Words } from './words';

/**
 * The jobs a chat handed to background agents (plan flow 2): each engine child run of a `code_task` hand-off is one
 * job, read from where it lives, never polled:
 *   - the parent's turn says a job exists, what it was asked and whether it still runs (its `sub_agent` part, kept
 *     current by the parent's own stream);
 *   - the job's own stream says what it did, step by step, and how it ended (`job-stream.ts`);
 *   - the parent's open asks say it is waiting on you (approvals the job raised reach the chat with `childRunId`);
 *   - one timeline read after it ends says what the stream does not: an operator's stop (G11).
 *
 * Pure; worded through `t`. The six status words are the design's, one meaning each.
 */

/** What the parent's turn says of one job. */
export interface JobRef {
  runId: string;
  parentRunId?: string;
  goal?: string;
  /** The engine named by a hand-off the run made on its own (DEEPROUTE). */
  engine?: string;
  partStatus: 'running' | 'completed' | 'failed' | 'cancelled';
  errorType?: string;
}

/** What is known of a job beyond its parent's part. */
export interface JobFacts {
  stream?: JobStreamState;
  /** The engine kind its timeline or brief names. */
  engine?: string | null;
  /** G11: an admin's stop, and its reason. */
  operatorStop?: { reason: string } | null;
  /** When the person list says it started and ended (ms). */
  createdAt?: number;
  endedAt?: number | null;
}

export interface JobView {
  runId: string;
  parentRunId?: string;
  title: string;
  goal?: string;
  agent: SandboxAgent;
  status: JobStatus;
  worker: SandboxWorker;
  state: WorkerState;
  startedAt?: number;
  finishedAt?: number;
  outputs: ReadonlyArray<{ name: string; artifactId?: string }>;
  report: ReadonlyArray<AnswerBlock>;
  /** "An admin stopped this: maintenance" (G11). */
  stoppedBy?: string;
  /** The step it is parked on while it needs you, and the steps so far. */
  asking?: { index: number; total: number; doing: string };
}

const PART_STATUS = new Set(['running', 'completed', 'failed', 'cancelled']);

/**
 * The engine jobs a turn's parts hold, in the order they were handed out: `sub_agent` parts of a `code_task`
 * hand-off (CTL-6) or of a hand-off the run made on its own (DEEPROUTE). A sub-agent that is not a job (a role
 * helper) and an engine's own helper are the trail's, not cards.
 */
export function jobRefsOfParts(parts: ReadonlyArray<MessagePart>): JobRef[] {
  const refs: JobRef[] = [];
  const visit = (list: ReadonlyArray<MessagePart>) => {
    for (const p of list) {
      if (p.type !== 'sub_agent') continue;
      // A durable loop sub-agent is a job too (it has a parent run): its panel is named by its goal (F10).
      const job = p.codeTaskGoal || p.codeTaskHandoff || p.parentRunId;
      if (p.runId && job && !p.engineHelper && !refs.some((r) => r.runId === p.runId)) {
        const status = PART_STATUS.has(String(p.status)) ? (p.status as JobRef['partStatus']) : 'running';
        refs.push({
          runId: p.runId,
          ...(p.parentRunId ? { parentRunId: p.parentRunId } : {}),
          ...(p.codeTaskGoal || p.goal ? { goal: p.codeTaskGoal ?? p.goal } : {}),
          ...(p.codeTaskHandoff?.engine ? { engine: p.codeTaskHandoff.engine } : {}),
          partStatus: status,
          ...(p.errorType ? { errorType: p.errorType } : {}),
        });
      }
      if (Array.isArray(p.parts)) visit(p.parts);
    }
  };
  visit(parts);
  return refs;
}

const STATUS_OF_PART: Record<JobRef['partStatus'], string> = { running: 'running', completed: 'completed', failed: 'failed', cancelled: 'cancelled' };

/** The approvals of a chat that one job raised, as `engineJob` reads them. */
function asksOf(ref: JobRef, facts: JobFacts, pending: ReadonlyArray<PendingApproval>) {
  const own = (facts.stream?.asks ?? []).map((a) => ({ ...a }));
  for (const a of pending) {
    if (a.childRunId !== ref.runId) continue;
    const status = a.status === 'sending' ? 'pending' : a.status === 'answered' ? 'approved' : a.status;
    const known = own.find((x) => x.interactionId === a.interactionId);
    if (known) {
      if (known.status === 'pending') known.status = status as typeof known.status;
    } else {
      own.push({ interactionId: a.interactionId, toolName: a.toolName, ...(a.displayName ? { displayName: a.displayName } : {}), argsSummary: a.argsSummary, status: status as typeof own[number]['status'] });
    }
  }
  return own;
}

export function jobView(ref: JobRef, facts: JobFacts, pending: ReadonlyArray<PendingApproval>, t: Words): JobView {
  const stream = facts.stream;
  // The parent's part is the authority on "it ended"; the stream says how, once it has seen the end. A part saved as
  // failed with `cancelled` was stopped, as its card reads it (`job-card-state.ts`): read here as a failure, the chat's
  // list said "Couldn't finish" beside the card's "Stopped by you" after a reload.
  const partStatus = ref.partStatus === 'failed' && ref.errorType === 'cancelled' ? 'cancelled' : STATUS_OF_PART[ref.partStatus];
  const status = stream?.ended ? stream.status : ref.partStatus !== 'running' ? partStatus : stream?.status ?? 'running';
  const job: EngineJob = engineJob(
    {
      runId: ref.runId,
      goal: ref.goal ?? null,
      engine: facts.engine ?? ref.engine ?? null,
      status: facts.operatorStop ? 'cancelled' : status,
      frames: stream?.frames ?? [],
      approvals: asksOf(ref, facts, pending),
      ...(stream?.startedAt ?? facts.createdAt ? { startedAt: stream?.startedAt ?? facts.createdAt } : {}),
      ...(stream?.finishedAt ?? facts.endedAt ? { finishedAt: (stream?.finishedAt ?? facts.endedAt) as number } : {}),
    },
    t,
  );
  const startedAt = stream?.startedAt ?? facts.createdAt;
  const finishedAt = stream?.finishedAt ?? facts.endedAt ?? undefined;
  const view: JobView = {
    runId: ref.runId,
    ...(ref.parentRunId ? { parentRunId: ref.parentRunId } : {}),
    title: job.worker.title,
    ...(ref.goal ? { goal: ref.goal } : {}),
    agent: job.worker.agent,
    status: job.status,
    worker: job.worker,
    state: job.state,
    ...(startedAt ? { startedAt } : {}),
    ...(finishedAt ? { finishedAt } : {}),
    outputs: job.worker.outputs,
    report: job.worker.report,
    ...(facts.operatorStop ? { stoppedBy: operatorStopWords(facts.operatorStop.reason, t) } : {}),
  };
  if (job.status === 'needsYou') {
    const index = Math.min(job.state.done, Math.max(0, job.worker.steps.length - 1));
    const step: SandboxStep | undefined = job.worker.steps[index];
    if (step) view.asking = { index, total: job.worker.steps.length, doing: step.activeTitle ?? step.title };
  }
  return view;
}

/** G11: "An admin stopped this: maintenance". Never the admin's note. */
export function operatorStopWords(reason: string, t: Words): string {
  const known = ['security', 'cost', 'incident', 'maintenance', 'other'].includes(reason) ? reason : 'other';
  return t('ui.job.adminStopped', { reason: t(`ui.job.stopReason.${known}`) });
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * "8s", "1m 04s", "1h 12m", "3 days": elapsed, as a person reads it, on every surface that shows a job's time (its
 * card, report, panel header and the lists). Past an hour the seconds no longer matter, past a day the hours; a
 * clock that only knew minutes read "54614m 26s" for a job five weeks old (BGUI-11).
 */
export function jobClock(ms: number, t: Words): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < MINUTE) return t('ui.job.clock.seconds', { s });
  if (s < HOUR) return t('ui.job.clock.minutes', { m: Math.floor(s / MINUTE), s: String(s % MINUTE).padStart(2, '0') });
  if (s < DAY) return t('ui.job.clock.hours', { h: Math.floor(s / HOUR), m: Math.floor((s % HOUR) / MINUTE) });
  return t('ui.job.clock.days', { count: Math.floor(s / DAY) });
}

const elapsed = (job: Pick<JobView, 'startedAt' | 'finishedAt'>, now: number): number | null =>
  job.startedAt ? (job.finishedAt ?? Math.max(now, job.startedAt)) - job.startedAt : null;

/** A card's status line, before " · agent": the word, and the clock where it means something. */
export function cardWords(job: JobView, now: number, t: Words): string {
  const ms = elapsed(job, now);
  switch (job.status) {
    case 'working':
      return ms === null ? t('ui.status.working') : t('ui.job.workingFor', { time: jobClock(ms, t) });
    case 'done':
      return ms === null ? t('ui.status.done') : t('ui.job.doneIn', { time: jobClock(ms, t) });
    default:
      return t(`ui.status.${job.status}`);
  }
}

/** A board row's line: where the job is, the step it is on while it works. */
export function boardWords(job: JobView, now: number, t: Words): string {
  const ms = elapsed(job, now);
  const step = job.worker.steps[job.state.done];
  switch (job.status) {
    case 'working': {
      const doing = step ? step.activeTitle ?? step.title : t('ui.status.working');
      return ms === null ? doing : t('ui.job.doingFor', { doing, time: jobClock(ms, t) });
    }
    case 'needsYou':
      return job.asking ? t('ui.job.needsYouAt', { doing: job.asking.doing }) : t('ui.status.needsYou');
    case 'stopped':
      return job.stoppedBy ?? t('ui.status.stopped');
    default:
      return cardWords(job, now, t);
  }
}

/** The open agent's header line: the word with the clock, or how it ended. */
export function headerWords(job: JobView, now: number, t: Words): string {
  const ms = elapsed(job, now);
  switch (job.status) {
    case 'needsYou':
      return ms === null ? t('ui.status.needsYou') : t('ui.job.needsYouFor', { time: jobClock(ms, t) });
    case 'stopped':
      return job.stoppedBy ?? t('ui.status.stopped');
    default:
      return cardWords(job, now, t);
  }
}

/** A report's byline: how it ended. Only "couldn't finish" spends a colour (the component's to paint). */
export function bylineWords(job: JobView, t: Words): string {
  const ms = elapsed(job, 0);
  switch (job.status) {
    case 'couldntFinish':
      return t('ui.job.byline.failed');
    case 'stopped':
      return job.stoppedBy ?? t('ui.job.byline.stopped');
    default:
      return ms === null ? t('ui.job.byline.done') : t('ui.job.byline.doneIn', { time: jobClock(ms, t) });
  }
}

export const isEnded = (s: JobStatus): boolean => s === 'done' || s === 'couldntFinish' || s === 'stopped';

/** Reports land in FINISH order (design §6B.42): the ended jobs, earliest end first, unknown ends last in hand-out order. */
export function reportsInFinishOrder(jobs: ReadonlyArray<JobView>): JobView[] {
  return jobs
    .map((job, i) => ({ job, i }))
    .filter(({ job }) => isEnded(job.status))
    .sort((a, b) => (a.job.finishedAt ?? Number.MAX_SAFE_INTEGER) - (b.job.finishedAt ?? Number.MAX_SAFE_INTEGER) || a.i - b.i)
    .map(({ job }) => job);
}

/** More than this and the chat shows one board, not a card each. */
export const CARDS_UP_TO = 3;

export type JobSection = 'needsYou' | 'working' | 'upNext' | 'finished';
export const JOB_SECTIONS: ReadonlyArray<JobSection> = ['needsYou', 'working', 'upNext', 'finished'];

export function sectionOf(status: JobStatus): JobSection {
  if (status === 'needsYou') return 'needsYou';
  if (status === 'working') return 'working';
  if (status === 'upNext') return 'upNext';
  return 'finished';
}
