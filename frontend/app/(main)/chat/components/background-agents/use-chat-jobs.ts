'use client';

import { useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { MessagePart, PendingApproval } from '../../types';
import { useChatStore } from '../../store';
import { followJob, useJobsStore } from '@/lib/harness-jobs/jobs-store';
import { jobView, type JobView } from '@/lib/harness-jobs/jobs';
import type { SessionRoute } from '@/lib/harness-jobs/jobs-api';
import type { Words } from '@/lib/harness-jobs/words';
import { chatJobRefs } from './chat-jobs';

const NO_PARTS: MessagePart[] = [];
const NO_APPROVALS: PendingApproval[] = [];

export interface ChatJobs {
  session: SessionRoute | null;
  /** Only the owner stops a job and reads its timeline (D14). */
  owner: boolean;
  jobs: JobView[];
  /** What was asked in the turn that handed out each job, by job. */
  asks: ReadonlyMap<string, string | null>;
}

/**
 * The chat on screen's background agents, live. Each job's status comes from the same place its card's does: the
 * chat's `sub_agent` part, joined with the job's own stream in the shared jobs store and the chat's open asks.
 * `follow` (the list is open) also follows each still-running job's stream, ref-counted and capped with the cards'
 * (`MAX_FOLLOWED`), so its tasks tick; nothing here polls.
 */
export function useChatJobs(follow: boolean): ChatJobs {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  // Field by field: the slot itself changes with every streamed token, these only when jobs can.
  const messages = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined));
  const streaming = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.streamingParts : undefined)) ?? NO_PARTS;
  const pending = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.pendingApprovals : undefined)) ?? NO_APPROVALS;
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const agentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  const owner = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner === true : false));
  const streams = useJobsStore((s) => s.streams);
  const extras = useJobsStore((s) => s.extras);

  const refs = useMemo(() => chatJobRefs(messages ?? [], streaming), [messages, streaming]);
  const session = useMemo<SessionRoute | null>(() => (convId ? { ...(agentId ? { agentId } : {}), sessionId: convId } : null), [convId, agentId]);

  const liveIds = refs
    .filter((r) => r.partStatus === 'running')
    .map((r) => r.runId)
    .join(',');
  useEffect(() => {
    if (!follow || !session || !liveIds) return undefined;
    const releases = liveIds.split(',').map((runId) => followJob(session, runId, { settle: owner }));
    return () => releases.forEach((release) => release());
    // `session` is named by its ids: a new object for the same chat must not restart every stream.
  }, [follow, liveIds, convId, agentId, owner]);

  return useMemo(() => {
    const asks = new Map<string, string | null>();
    const jobs = refs.map((ref) => {
      asks.set(ref.runId, ref.ask);
      const extra = extras[ref.runId];
      return jobView(ref, { stream: streams[ref.runId], engine: extra?.engine ?? null, operatorStop: extra?.operatorStop ?? null }, pending, words);
    });
    return { session, owner, jobs, asks };
  }, [refs, streams, extras, pending, words, session, owner]);
}
