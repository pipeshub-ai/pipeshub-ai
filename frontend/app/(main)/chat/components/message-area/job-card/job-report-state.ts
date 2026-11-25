import type { TFunction } from 'i18next';
import type { CutShortReason } from '@/lib/contracts/harness/generated/core/enums';
import { jobFiles } from '@/lib/harness-jobs/job-files';
import { cutShortReason } from '../../../cut-short';
import { runFailureWords } from '../../../run-failure-words';
import type { ChatArtifact, MessagePart, RunAnswerCitation, SSEEngineProgressEvent } from '../../../types';
import type { JobStreamState, JobTimeline } from './data';
import { clockWords, elapsedMs, jobCardState, type JobCardState } from './job-card-state';

/**
 * What a finished job reports in the chat (design `sandbox-thread.tsx` JobReport and `sandbox-report.ts`, §6B.42):
 * how it ended and what it delivered (BGUI-11: never its raw reply, which the chat's answer sums up). Pure, from the same facts its card reads (its parent's part, its own
 * stream, the one timeline read after it ended), with the card's own `jobCardState`, so the report and the card
 * never say different things about the same job.
 */

export interface JobReportInput {
  part: MessagePart;
  stream?: JobStreamState;
  timeline?: JobTimeline | null;
  operatorStop?: { reason: string } | null;
  isOwner: boolean;
  /** When it ended, from its request's jobs read: the order of a reload, whose streams are not replayed. */
  endedAt?: number | null;
}

export interface JobReportFacts {
  runId: string;
  state: JobCardState;
  /** It answered from what it had when a limit cut it short: what it left is partial (BUDGETEND, SPENDCONT). */
  partial: CutShortReason | null;
  /** The files it delivered, as the answer's file cards draw them. */
  delivered: ChatArtifact[];
  /** The changed files it only worked with (`lib/harness-jobs/job-files.ts`); null while not known. */
  working: number | null;
  finishedAt?: number;
  elapsedMs: number | null;
}

const ENDED: ReadonlySet<JobCardState['status']> = new Set(['done', 'couldntFinish', 'stopped']);

/** The engine frames the job left: its stream's while followed, else its timeline's, else its parent's copy. */
function framesOf(input: JobReportInput): ReadonlyArray<SSEEngineProgressEvent> {
  if (input.stream?.frames.length) return input.stream.frames;
  if (input.timeline?.engine?.frames.length) return input.timeline.engine.frames;
  return (input.part.parts ?? []).flatMap((p) => (p.type === 'engine_task' ? (p.frames ?? []) : []));
}

const dataOf = (f: SSEEngineProgressEvent): Record<string, unknown> => (f.data ?? {}) as Record<string, unknown>;

/**
 * Its own reply: its run's final text, else the engine's `finished` line when that line is not a failure's. Shown
 * only through `JobReply` (the shared answer rendering), in its panel.
 */
export function jobReplyOf(
  timeline: JobTimeline | null | undefined,
  frames: ReadonlyArray<SSEEngineProgressEvent>,
): { text: string | null; citations: RunAnswerCitation[]; cut: CutShortReason | null } {
  const final = timeline?.parts.find((p) => p.type === 'text' && p.isFinal === true);
  const cut = cutShortReason(timeline?.digest?.cutShortBy) ?? cutShortReason(final?.truncatedBy);
  const said = final?.content?.trim();
  // The answer's citations number its final text's links; the engine's `finished` line has none of its own.
  if (said) return { text: said, citations: timeline?.answer?.citations ?? [], cut };
  const finished = [...frames].reverse().find((f) => f.kind === 'finished');
  const line = finished && dataOf(finished).success !== false ? finished.text.trim() : '';
  return { text: line || null, citations: [], cut };
}

/** The report of a job that ended; null while it still goes (its card says how it is doing). */
export function jobReportFacts(input: JobReportInput): JobReportFacts | null {
  const state = jobCardState({ part: input.part, stream: input.stream, operatorStop: input.operatorStop ?? null, isOwner: input.isOwner });
  if (!ENDED.has(state.status) || !input.part.runId) return null;
  const frames = framesOf(input);
  const { cut } = jobReplyOf(input.timeline, frames);
  const files = jobFiles(frames, input.timeline?.digest);
  const limit: CutShortReason | null = state.stopCause === 'budget' ? 'budget' : state.stopCause === 'time' ? 'time' : null;
  const finishedAt = input.stream?.finishedAt ?? input.endedAt ?? undefined;
  return {
    runId: input.part.runId,
    state,
    partial: cut ?? limit,
    delivered: files.delivered,
    working: files.working,
    ...(finishedAt !== undefined ? { finishedAt } : {}),
    elapsedMs: elapsedMs(input.stream, { durationMs: input.part.durationMs ?? input.timeline?.digest?.durationMs ?? undefined }, 0),
  };
}

/** Reports land in FINISH order: earliest end first; an end not known yet goes last, in hand-out order. */
export function inFinishOrder<T extends { finishedAt?: number }>(reports: ReadonlyArray<T>): T[] {
  return reports
    .map((report, i) => ({ report, i }))
    .sort((a, b) => (a.report.finishedAt ?? Number.MAX_SAFE_INTEGER) - (b.report.finishedAt ?? Number.MAX_SAFE_INTEGER) || a.i - b.i)
    .map(({ report }) => report);
}

const ADMIN_REASONS = ['security', 'cost', 'incident', 'maintenance', 'other'] as const;

/**
 * How it ended, as its byline says it after its name: "finished in 18s", "couldn't finish", "was stopped by you".
 * Never a code; a stop says who or what stopped it (D12), as its card does.
 */
export function reportOutcome(facts: Pick<JobReportFacts, 'state' | 'elapsedMs'>, operatorStop: { reason: string } | null | undefined, t: TFunction): string {
  const { state } = facts;
  if (state.status === 'couldntFinish') return t('jobReport.couldntFinish');
  if (state.status === 'stopped') {
    switch (state.stopCause) {
      case 'you':
        return t('jobReport.stoppedByYou');
      case 'admin': {
        const reason = (ADMIN_REASONS as readonly string[]).includes(operatorStop?.reason ?? '') ? operatorStop?.reason : 'other';
        return t('jobReport.stoppedByAdmin', { reason: t(`jobCard.adminReason.${reason}`) });
      }
      case 'budget':
        return t('jobReport.outOfBudget');
      case 'time':
        return t('jobReport.outOfTime');
      default:
        return t('jobReport.stopped');
    }
  }
  return facts.elapsedMs === null ? t('jobReport.finished') : t('jobReport.finishedIn', { time: clockWords(facts.elapsedMs, t) });
}

/** Why it couldn't finish, in the chat's own failure words; null for every other ending. */
export function failureLine(state: JobCardState): string | null {
  return state.status === 'couldntFinish' ? runFailureWords(state.failure ?? null, null) : null;
}
