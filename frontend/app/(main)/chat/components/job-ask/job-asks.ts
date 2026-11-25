import { create } from 'zustand';
import { latestSignIn, type EngineSignInLine } from '../../engine-signin';
import { approvalKey, groupApprovals, isOpenApproval, type ApprovalGroup } from '../../interactions';
import type { PendingApproval, SSEEngineProgressEvent } from '../../types';

/**
 * A job's asks float over the message box (BGUI-0, design `needs-you-card.tsx`): the approvals a background job
 * raised (they reach the chat with `childRunId`) and its engine's sign-in. The root run's own asks stay at their row.
 *
 * One answer state sits behind both places: the approvals are the chat slot's own `pendingApprovals`, decided through
 * `decideApproval`, and the sign-in is the job's own stream. The transcript draws a quiet pointer for an ask while
 * the floating card lists it, and the ask's stamp once it is answered, so an ask is never answered twice nor shown
 * open in one place and answered in the other.
 */

export type JobAskItem =
  | { key: string; kind: 'approval'; runId: string; group: ApprovalGroup }
  | { key: string; kind: 'signin'; runId: string; line: EngineSignInLine };

export interface JobAskSnapshot {
  isStreaming: boolean;
  pendingApprovals: ReadonlyArray<PendingApproval>;
  /** The chat's live jobs, with the frames their own streams carried. */
  jobs: ReadonlyArray<{ runId: string; frames: ReadonlyArray<SSEEngineProgressEvent> }>;
}

export const signInKey = (runId: string, attemptId: string): string => `engine:${runId}:${attemptId}`;

/** The chat's open job asks, in the order they were asked; sign-ins after the approvals. */
export function jobAsksOf(snapshot: JobAskSnapshot): JobAskItem[] {
  const items: JobAskItem[] = [];
  // Asks live as long as their run, as the inline cards read them: once it ended its resolution frames close them.
  const open = snapshot.isStreaming ? snapshot.pendingApprovals.filter((a) => a.childRunId && isOpenApproval(a)) : [];
  for (const group of groupApprovals(open)) {
    items.push({ key: group.key, kind: 'approval', runId: group.items[0].childRunId as string, group });
  }
  for (const job of snapshot.jobs) {
    const line = latestSignIn([...job.frames]);
    if (line?.state === 'requested') items.push({ key: signInKey(job.runId, line.attemptId), kind: 'signin', runId: job.runId, line });
  }
  return items;
}

/** The keys a list of asks answers to: each approval's own interaction, and each sign-in. */
export function floatKeys(items: ReadonlyArray<JobAskItem>): string[] {
  return items.flatMap((item) => (item.kind === 'approval' ? item.group.items.map(approvalKey) : [item.key]));
}

/** The key a pointer in the transcript names its group by: its first open call. */
export const pointerKey = (group: ApprovalGroup): string | null => {
  const open = group.items.find(isOpenApproval);
  return open ? approvalKey(open) : null;
};

/** An inline card the floating card already holds: one of its open calls is listed there. */
export const floatsGroup = (group: ApprovalGroup, floated: ReadonlySet<string>): boolean =>
  floated.size > 0 && group.items.some((a) => isOpenApproval(a) && floated.has(approvalKey(a)));

interface FloatState {
  /** What the floating card lists now; empty when it is not on screen, so the transcript draws every ask itself. */
  floated: ReadonlySet<string>;
  /** An ask a pointer asked to be shown: the floating card turns to it and focuses it. */
  wanted: string | null;
  setFloated: (keys: ReadonlyArray<string>) => void;
  show: (key: string | null) => void;
}

const NONE: ReadonlySet<string> = new Set();

export const useJobAskFloat = create<FloatState>((set, get) => ({
  floated: NONE,
  wanted: null,
  setFloated: (keys) => {
    const now = get().floated;
    if (keys.length === now.size && keys.every((k) => now.has(k))) return;
    set({ floated: keys.length ? new Set(keys) : NONE });
  },
  show: (key) => set({ wanted: key }),
}));

/** The asks the floating card holds now. */
export const useFloatedAsks = (): ReadonlySet<string> => useJobAskFloat((s) => s.floated);
