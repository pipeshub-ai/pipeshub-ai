import type { ThreadMessageLike } from '@assistant-ui/react';
import type { MessagePart } from '../../types';
import { isLive } from '@/lib/harness-jobs/job-status';
import { jobRefsOfParts, type JobRef, type JobView } from '@/lib/harness-jobs/jobs';
import { jobRowOfView, jobsSections, type JobRow, type JobsSection } from '@/lib/harness-jobs/jobs-list';
import type { Words } from '@/lib/harness-jobs/words';

/**
 * A chat's own background agents (design `jobs-panel.tsx`, §6B.42–43), as the top bar's list reads them. Pure: the
 * jobs come from the chat's own `sub_agent` parts (saved and streaming), the same parts its cards are drawn from, so
 * the list and the cards never disagree on which jobs exist.
 */

/** One job the chat holds, with what the person asked in the turn that handed it out. */
export interface ChatJobRef extends JobRef {
  /** The user message of that turn; null when the chat on screen does not hold it. */
  ask: string | null;
}

function textOf(message: ThreadMessageLike): string | null {
  const { content } = message;
  if (typeof content === 'string') return content.trim() || null;
  const text = content
    .map((p) => (p.type === 'text' ? p.text : ''))
    .join(' ')
    .trim();
  return text || null;
}

function partsOf(message: ThreadMessageLike): MessagePart[] {
  const saved = (message.metadata?.custom as { persistedParts?: unknown } | undefined)?.persistedParts;
  return Array.isArray(saved) ? (saved as MessagePart[]) : [];
}

/** Every job the chat handed out, oldest turn first; the streaming turn's last, asked by the latest user message. */
export function chatJobRefs(messages: ReadonlyArray<ThreadMessageLike>, streaming: ReadonlyArray<MessagePart>): ChatJobRef[] {
  const refs: ChatJobRef[] = [];
  const seen = new Set<string>();
  let ask: string | null = null;
  const take = (parts: ReadonlyArray<MessagePart>) => {
    for (const ref of jobRefsOfParts(parts)) {
      if (seen.has(ref.runId)) continue;
      seen.add(ref.runId);
      refs.push({ ...ref, ask });
    }
  };
  for (const message of messages) {
    if (message.role === 'user') ask = textOf(message);
    else take(partsOf(message));
  }
  take(streaming);
  return refs;
}

/** What the top-bar button shows: nothing without jobs, a count while any still goes, amber when one needs you. */
export interface OpenerState {
  shown: boolean;
  live: number;
  needsYou: number;
}

export function openerState(jobs: ReadonlyArray<Pick<JobView, 'status'>>): OpenerState {
  return {
    shown: jobs.length > 0,
    live: jobs.filter((j) => isLive(j.status)).length,
    needsYou: jobs.filter((j) => j.status === 'needsYou').length,
  };
}

/** The list's sections, each request's jobs on one card titled by what was asked. */
export function chatJobSections(
  jobs: ReadonlyArray<JobView>,
  asks: ReadonlyMap<string, string | null>,
  session: { sessionId: string; agentKey?: string },
  t: Words,
  cleared: ReadonlySet<string>,
): JobsSection[] {
  const rows: JobRow[] = jobs.map((j) => jobRowOfView(j, session.sessionId, session.agentKey));
  const sections = jobsSections(rows, t, cleared);
  for (const section of sections) {
    for (const group of section.groups) {
      if (group.title === undefined) continue;
      group.title = asks.get(group.rows[0].runId)?.trim() || t('ui.jobs.request');
    }
  }
  return sections;
}

/** One step of an agent's own work, as its "N tasks" fold lists it. */
export interface TaskLine {
  id: string;
  title: string;
  mark: 'done' | 'now' | 'todo';
}

/**
 * An agent's tasks: the steps its own stream reported, ticked off as it goes, the one in hand spinning while it
 * still runs. An agent's stream names the steps it took, not a plan ahead of them, so there are rings only for a
 * step it was parked before (an ask it waits on).
 */
export function tasksOf(job: Pick<JobView, 'status' | 'worker' | 'state'>): TaskLine[] {
  const live = isLive(job.status) && job.status !== 'upNext';
  return job.worker.steps
    .filter((step) => !job.state.skipped.includes(step.id))
    .map((step, i) => {
      const mark: TaskLine['mark'] = i < job.state.done ? 'done' : i === job.state.done && live ? 'now' : live ? 'todo' : 'done';
      return { id: step.id, title: mark === 'now' ? step.activeTitle ?? step.title : step.title, mark };
    });
}

/** How many tasks an open fold lists before "Show N earlier": the latest ones, where the agent is. */
export const TASKS_SHOWN = 20;
