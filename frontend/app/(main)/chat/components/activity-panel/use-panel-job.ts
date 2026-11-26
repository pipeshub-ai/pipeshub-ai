'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useChatStore } from '../../store';
import { budgetRefusalKey } from '../../code-tasks';
import type { ActivityView, ChatArtifact, MessagePart, PendingApproval, RunAnswerCitation, RunTimelineView, SSEEngineProgressEvent } from '../../types';
import { jobDeliverables } from '@/lib/harness-jobs/job-files';
import { jobReplyOf } from '../message-area/job-card/job-report-state';
import type { SandboxAgent } from '@/lib/harness-jobs/view-models';
import type { PanelRun } from './activity-panel-store';
import {
  followJob,
  isLive,
  jobClock,
  jobRefsOfParts,
  jobStatusOf,
  jobStatusWord,
  jobView,
  operatorStopWords,
  useJobsStore,
  type JobAsk,
  type JobRef,
  type JobStatus,
  type JobTimeline,
  type Words,
} from './job-data';

const NO_PARTS: MessagePart[] = [];
const NO_APPROVALS: PendingApproval[] = [];

/** Every job the chat on screen holds in its `sub_agent` parts, saved and streaming, where a job's card gets its goal. */
export function useChatJobRefs(): JobRef[] {
  const messages = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined));
  const streaming = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.streamingParts ?? NO_PARTS) : NO_PARTS));
  return useMemo(() => {
    const parts: MessagePart[] = [...streaming];
    for (const message of messages ?? []) {
      const saved = (message.metadata?.custom as { persistedParts?: unknown } | undefined)?.persistedParts;
      if (Array.isArray(saved)) parts.push(...(saved as MessagePart[]));
    }
    return jobRefsOfParts(parts);
  }, [messages, streaming]);
}

function useJobRef(runId: string): JobRef | null {
  const refs = useChatJobRefs();
  return useMemo(() => refs.find((ref) => ref.runId === runId) ?? null, [refs, runId]);
}

/** Whether the chat's part for the job says it ended; null while the chat on screen holds no part for it. */
export function usePartEnded(runId: string): boolean | null {
  const ref = useJobRef(runId);
  return ref === null ? null : ref.partStatus !== 'running';
}

const NO_FRAMES: ReadonlyArray<SSEEngineProgressEvent> = [];

/** A second's tick, only while something on screen counts time. */
function useNow(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

export interface PanelJob {
  title: string;
  /** Who does it, by its maker's name ("Claude Code"): the panel's header names the agent, the breadcrumb the job. */
  agent: SandboxAgent;
  status: JobStatus;
  /** The status in words with its clock or its reason: "Working · 2m 14s", "Stopped by you". */
  line: string;
  /** Still going: Stop is offered and the clock ticks. */
  live: boolean;
  /** Asks it waits on, answered on its card in the chat (D3). */
  asks: ReadonlyArray<JobAsk>;
  /** What it delivered (`lib/harness-jobs/job-files.ts`): from its stream while it runs, its summary once it ended. */
  outputs: ReadonlyArray<ChatArtifact>;
  /** Its own reply once it ended, drawn only through `JobReply`; null while it runs or when it gave none. */
  reply: { text: string; citations: ReadonlyArray<RunAnswerCitation> } | null;
  /** Its own stream's engine frames: an engine job's steps, the same its card draws (D13). */
  frames: ReadonlyArray<SSEEngineProgressEvent>;
}

const ACTIVITY_STATUS: Record<ActivityView['status'], string> = {
  queued: 'queued',
  running: 'running',
  waiting: 'suspended',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};

/** The asks it waits on: its stream's, and the chat's open approvals it raised. */
function pendingAsks(own: ReadonlyArray<JobAsk>, chat: ReadonlyArray<PendingApproval>, runId: string): JobAsk[] {
  const asks = own.filter((a) => a.status === 'pending');
  for (const a of chat) {
    if (a.childRunId !== runId || (a.status !== 'pending' && a.status !== 'sending')) continue;
    if (asks.some((x) => x.interactionId === a.interactionId)) continue;
    asks.push({ interactionId: a.interactionId, toolName: a.toolName, ...(a.displayName ? { displayName: a.displayName } : {}), argsSummary: a.argsSummary ?? null, status: 'pending' });
  }
  return asks;
}

/** Literal keys, so `i18n:keys` checks each one. */
function stoppedWords(t: TFunction, operatorStop: { reason: string } | null | undefined, words: Words): string {
  return operatorStop ? operatorStopWords(operatorStop.reason, words) : t('jobPanel.stoppedByYou');
}

/** The status line, as the card's (D12): who or what stopped it, never a code. */
export function jobStatusLine(
  t: TFunction,
  words: Words,
  facts: { status: JobStatus; elapsedMs: number | null; operatorStop?: { reason: string } | null; errorType?: string | null },
): string {
  const clock = facts.elapsedMs === null ? null : jobClock(facts.elapsedMs, words);
  switch (facts.status) {
    case 'stopped':
      return stoppedWords(t, facts.operatorStop, words);
    case 'couldntFinish': {
      const reason = budgetRefusalKey(facts.errorType);
      return reason ? t('jobPanel.withReason', { status: jobStatusWord('couldntFinish', words), reason: t(reason) }) : jobStatusWord('couldntFinish', words);
    }
    default:
      return clock ? t('jobPanel.withTime', { status: jobStatusWord(facts.status, words), time: clock }) : jobStatusWord(facts.status, words);
  }
}

/**
 * One job as its panel's header reads it. Its own stream (shared with its card, ref-counted, D13) says how it
 * stands; the activity view stands in while the stream waits its turn; the timeline the panel reads anyway brings
 * an admin's stop and, once it ended, what it made.
 */
export function usePanelJob(run: PanelRun, activity: ActivityView | null, timeline: RunTimelineView | null): PanelJob {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const ref = useJobRef(run.runId);
  const pending = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.pendingApprovals ?? NO_APPROVALS) : NO_APPROVALS));
  const stream = useJobsStore((s) => s.streams[run.runId]);
  const { agentId, sessionId, runId } = run;
  // Only its owner reads the job's timeline (D14); a viewer's panel follows the stream for the header alone.
  const isOwner = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner === true : false));
  useEffect(() => followJob({ ...(agentId ? { agentId } : {}), sessionId }, runId, { settle: isOwner }), [agentId, sessionId, runId, isOwner]);

  const operatorStop = (timeline as JobTimeline | null)?.operatorStop ?? null;
  const view = useMemo(() => {
    const fallback: JobRef = { runId: run.runId, partStatus: 'running' };
    return jobView(ref ?? fallback, { stream, operatorStop }, pending, words);
  }, [ref, run.runId, stream, operatorStop, pending, words]);

  // Before its stream has said anything, the part (when the chat holds it) or the activity view says how it stands.
  const heard = Boolean(stream && (stream.frames.length > 0 || stream.ended || stream.status !== 'queued'));
  const status: JobStatus = heard || (ref !== null && ref.partStatus !== 'running')
    ? view.status
    : activity
      ? jobStatusOf(operatorStop ? 'cancelled' : ACTIVITY_STATUS[activity.status])
      : view.status;
  const live = isLive(status);
  const now = useNow(live && view.startedAt !== undefined);
  const elapsedMs = view.startedAt ? (view.finishedAt ?? Math.max(now, view.startedAt)) - view.startedAt : timeline?.digest?.durationMs ?? null;

  // Its own stream's frames while followed, else the ones its timeline kept: what it delivered and said, one rule
  // with its report under the card (BGUI-11).
  const frames = stream?.frames.length ? stream.frames : (timeline?.engine?.frames ?? NO_FRAMES);
  const outputs = useMemo(() => jobDeliverables(frames, timeline?.digest), [frames, timeline]);
  const reply = useMemo(() => {
    if (live) return null;
    const { text, citations } = jobReplyOf(timeline as JobTimeline | null, frames);
    return text ? { text, citations } : null;
  }, [live, timeline, frames]);

  return {
    // The card shortens the goal to fit a row; the panel has the room for all of it.
    title: ref?.goal?.trim() || view.title,
    agent: view.agent,
    status,
    line: jobStatusLine(t, words, { status, elapsedMs, operatorStop, errorType: ref?.errorType ?? stream?.errorCode ?? null }),
    live,
    asks: status === 'needsYou' ? pendingAsks(stream?.asks ?? [], pending, run.runId) : [],
    outputs,
    reply,
    frames: stream?.frames ?? NO_FRAMES,
  };
}
