'use client';

import { ChatApi, type StreamMessageCallbacks } from './api';
import { useChatStore } from './store';
import { approvalEffectOf } from './approval-preview';
import { approvalKey } from './interactions';
import { cancelCardSend, cardSendRunning, cardSendState, sendCard, type SendAttempt, type SendFailure } from './card-send';
import type {
  ApprovalDecision,
  ApprovalScope,
  PendingApproval,
  SSEApprovalRequestedEvent,
  SSEApprovalResolvedEvent,
} from './types';

/** What names one card: its root run, the sub-agent run that asked (if any), and the interaction. */
export type ApprovalKey = Pick<PendingApproval, 'interactionId' | 'childRunId' | 'runId'>;

/**
 * An interaction's identity. Ids are unique within one run only: a sub-agent's may repeat the parent's (UX3), and
 * a later run of the conversation asks `ix.approve.t2.0` again (UXINT-0 R1).
 */
const sameInteraction = (a: ApprovalKey, b: ApprovalKey): boolean =>
  a.interactionId === b.interactionId &&
  (a.childRunId ?? '') === (b.childRunId ?? '') &&
  (a.runId ?? '') === (b.runId ?? '');

const DECIDED: ReadonlySet<string> = new Set(['approved', 'denied', 'answered']);

/** The run asked again (a replayed stream, a resubscription): one card per interaction. */
export function withApprovalRequested(
  approvals: PendingApproval[],
  data: SSEApprovalRequestedEvent & { runId?: string },
): PendingApproval[] {
  if (approvals.some((a) => sameInteraction(a, data))) return approvals;
  const { effect: raw, ...card } = data;
  const effect = approvalEffectOf(raw);
  return [...approvals, { ...card, ...(effect ? { effect } : {}), status: 'pending', errorCode: null }];
}

export function withApprovalResolved(
  approvals: PendingApproval[],
  data: SSEApprovalResolvedEvent & { runId?: string },
): PendingApproval[] {
  const status: PendingApproval['status'] = DECIDED.has(data.decision)
    ? (data.decision as ApprovalDecision)
    : 'closed';
  // G-12: a connect wait that ran out says "not connected in time", which its saved part says too.
  const ended = (a: PendingApproval): PendingApproval['status'] =>
    a.kind === 'connect' && data.decision === 'expired' ? 'expired' : status;
  return approvals.map((a) =>
    sameInteraction(a, data)
      ? { ...a, status: ended(a), errorCode: null, ...(data.scope === 'conversation' ? { scope: 'conversation' as const } : {}) }
      : a,
  );
}

/** WP-100: another approval of an ask that needs several was counted; the open card says how many it has. */
export function withApprovalCounted(
  approvals: PendingApproval[],
  data: { interactionId: string; approvals: { counted: number; required: number } },
): PendingApproval[] {
  return approvals.map((a) =>
    !a.childRunId && a.interactionId === data.interactionId && (a.status === 'pending' || a.status === 'sending')
      ? { ...a, approvals: { ...data.approvals } }
      : a,
  );
}

function updateApprovals(
  slotId: string,
  change: (approvals: PendingApproval[], runId: string | undefined) => PendingApproval[],
): void {
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return;
  useChatStore.getState().updateSlot(slotId, {
    pendingApprovals: change(slot.pendingApprovals ?? [], slot.durableRun?.runId),
  });
}

/**
 * The cards of a run the page resumes after frames it already saw (a reload with a saved stream): the stream will
 * not send their `approval_requested` again, so the run's timeline names the ones still waiting.
 */
export function seedPendingApprovals(slotId: string, listed: ReadonlyArray<SSEApprovalRequestedEvent & { status: string }>): void {
  updateApprovals(slotId, (list, runId) =>
    listed
      .filter((a) => a.status === 'pending')
      .reduce((acc, a) => withApprovalRequested(acc, { ...a, runId }), list),
  );
}

/** The stream callbacks that keep a slot's approval cards in step with the run. */
export function approvalStreamCallbacks(
  slotId: string,
): Pick<StreamMessageCallbacks, 'onApprovalRequested' | 'onApprovalResolved' | 'onApprovalCounted' | 'onInteractionWaiting'> {
  return {
    onInteractionWaiting: (interactionWaiting) => {
      if (useChatStore.getState().slots[slotId]) useChatStore.getState().updateSlot(slotId, { interactionWaiting });
    },
    onApprovalRequested: (data) =>
      updateApprovals(slotId, (list, runId) => withApprovalRequested(list, { ...data, runId })),
    onApprovalResolved: (data) => {
      // Decided elsewhere (another tab, an approver, the run ended): a send still waiting to retry stops.
      // A first attempt in flight finishes as usual; it carries what the frame does not (a deny reason, edits).
      const key = approvalSendKey({ ...data, runId: useChatStore.getState().slots[slotId]?.durableRun?.runId });
      if (cardSendState(key)) cancelCardSend(key);
      updateApprovals(slotId, (list, runId) => withApprovalResolved(list, { ...data, runId }));
    },
    onApprovalCounted: (data) => updateApprovals(slotId, (list) => withApprovalCounted(list, data)),
  };
}

/**
 * Runs the person decided on in this tab: their steps stay open when the run ends, so the receipt stays where
 * they acted (UXINT-1). A reload folds them again, as any finished answer.
 */
export const decidedRuns = new Set<string>();

/** The refusal of a decision, as the shared send reads it: an older mock or caller without `failure` is a refusal. */
const decisionAttempt = <T,>(sent: { refused: string; failure?: SendFailure } | T): SendAttempt<T> =>
  typeof sent === 'object' && sent !== null && 'refused' in sent
    ? { failed: sent.failure ?? { retry: false, code: sent.refused } }
    : { sent: sent as T };

/** The call's arguments as each kind of decision always sent them: only up to the last one it sets. */
function withoutTrailingUndefined(
  ...args: Parameters<typeof ChatApi.decideDurableInteractionOutcome>
): Parameters<typeof ChatApi.decideDurableInteractionOutcome> {
  let end = args.length;
  while (end > 4 && args[end - 1] === undefined) end -= 1;
  return args.slice(0, end) as Parameters<typeof ChatApi.decideDurableInteractionOutcome>;
}

/** What a card's send names it by: the card's own key (`approvalKey`), which its notice reads. */
export const approvalSendKey = (card: ApprovalKey): string => approvalKey(card);

/**
 * Sends the user's decision through the card's shared send (CARDFAIL): a transient failure retries with backoff
 * while the card keeps the person's choice, then offers Try again; a refusal is said on the card. The idempotency
 * key is the decision itself, so a retry replays the first answer instead of deciding twice.
 */
export async function decideApproval(
  slotId: string,
  card: ApprovalKey,
  decision: ApprovalDecision,
  scope: ApprovalScope = 'once',
  /** AP-05: why the person denied it, told to the run; ignored for any other decision. */
  reason?: string,
  /** AP-06: the arguments the person approves instead; only with an approval of this one call. */
  edits?: Record<string, unknown>,
): Promise<void> {
  const root = useChatStore.getState().slots[slotId]?.durableRun;
  if (!root) return;
  const { interactionId, childRunId } = card;
  const denyReason = decision === 'denied' ? reason?.trim() || undefined : undefined;
  // Decided on the run that asked: a sub-agent's on its own run in the same session (UX3), a root card on its run.
  const run = { ...root, runId: childRunId ?? card.runId ?? root.runId };
  const isThis = (a: PendingApproval) => sameInteraction(a, card);
  const asked = useChatStore.getState().slots[slotId]?.pendingApprovals?.find(isThis);
  const key = approvalSendKey(card);
  // BGUI-4: a job's ask can be answered from its floating card and from its row; the second answer is not sent.
  if ((asked && asked.status !== 'pending') || cardSendRunning(key)) return;
  decidedRuns.add(card.runId ?? root.runId);
  updateApprovals(slotId, (list) =>
    list.map((a) => (isThis(a) ? { ...a, status: 'sending', errorCode: null } : a)),
  );
  const conversation = scope === 'conversation' && decision === 'approved';
  const remember = scope === 'tool' && decision !== 'answered';
  const edited = edits !== undefined && decision === 'approved' && scope === 'once' ? edits : undefined;
  const [idempotencyKey, wireScope] = edited
    ? [`${interactionId}:${decision}:edited`, undefined]
    : conversation
      ? [`${interactionId}:${decision}:conversation`, 'conversation' as const]
      : remember
        ? [`${interactionId}:${decision}:remember`, 'tool' as const]
        : [`${interactionId}:${decision}`, undefined];
  const outcome = await sendCard(
    key,
    async () =>
      decisionAttempt(await ChatApi.decideDurableInteractionOutcome(...withoutTrailingUndefined(run, interactionId, decision, idempotencyKey, wireScope, denyReason, edited))),
    { again: () => void decideApproval(slotId, card, decision, scope, reason, edits) },
  );
  if ('cancelled' in outcome) return;
  // Counted on an ask that needs several approvals and still waits for the rest: the card stays open, with the count.
  const counted = 'sent' in outcome ? outcome.sent.awaiting : undefined;
  updateApprovals(slotId, (list) =>
    list.map((a) => {
      if (!isThis(a) || a.status !== 'sending') return a;
      if ('stopped' in outcome) return { ...a, status: 'pending', errorCode: null };
      if ('refused' in outcome) return { ...a, status: 'pending', errorCode: outcome.refused ?? 'HTTP_AGENT_HARNESS_DECISION_FAILED' };
      if (counted !== undefined) {
        return { ...a, status: 'pending', errorCode: null, approvedByYou: true, approvals: counted };
      }
      return {
        ...a,
        status: decision,
        errorCode: null,
        ...(conversation ? { scope: 'conversation' as const } : {}),
        ...(denyReason ? { denyReason } : {}),
        ...(edited ? { editedArgs: JSON.stringify(edited) } : {}),
      };
    }),
  );
}

/**
 * "Allow all N" / "Deny all" (DECIDE409): the batch's cards in one request, so the run wakes once to every
 * decision and the cards cannot race each other. A card someone already decided shows that decision. The batch is
 * one send, named by its first card, which the batch card reads.
 */
export async function decideApprovals(
  slotId: string,
  cards: readonly ApprovalKey[],
  decision: Extract<ApprovalDecision, 'approved' | 'denied'>,
  scope: ApprovalScope = 'once',
  reason?: string,
): Promise<void> {
  const root = useChatStore.getState().slots[slotId]?.durableRun;
  const [first] = cards;
  const denyReason = decision === 'denied' ? reason?.trim() || undefined : undefined;
  if (!root || !first) return;
  const listed = useChatStore.getState().slots[slotId]?.pendingApprovals ?? [];
  // BGUI-4: a call already being decided, or decided, elsewhere is left out; with none left nothing is sent.
  const undecided = cards.filter((card) => {
    const known = listed.find((a) => sameInteraction(a, card));
    return (!known || known.status === 'pending') && !cardSendRunning(approvalSendKey(card));
  });
  if (undecided.length === 0) return;
  // A batch card groups calls of one run (and one sub-agent run): posted to the run that asked.
  const run = { ...root, runId: first.childRunId ?? first.runId ?? root.runId };
  const isOne = (a: PendingApproval) => undecided.some((card) => sameInteraction(a, card));
  decidedRuns.add(first.runId ?? root.runId);
  updateApprovals(slotId, (list) =>
    list.map((a) => (isOne(a) ? { ...a, status: 'sending', errorCode: null } : a)),
  );
  const conversation = scope === 'conversation' && decision === 'approved';
  const ids = undecided.map((card) => card.interactionId);
  const outcome = await sendCard(
    approvalSendKey(undecided[0]),
    async () =>
      decisionAttempt(
        await ChatApi.decideDurableInteractions(
          run,
          ids,
          decision,
          // The server's command id already names the interactions; the key names the decision, as a card's does.
          `batch:${decision}${conversation ? ':conversation' : ''}`,
          conversation ? 'conversation' : undefined,
          ...(denyReason ? [denyReason] : []),
        ),
      ),
    { again: () => void decideApprovals(slotId, cards, decision, scope, reason) },
  );
  if ('cancelled' in outcome) return;
  updateApprovals(slotId, (list) =>
    list.map((a) => {
      if (!isOne(a) || a.status !== 'sending') return a;
      if ('stopped' in outcome) return { ...a, status: 'pending', errorCode: null };
      if ('refused' in outcome) return { ...a, status: 'pending', errorCode: outcome.refused ?? 'HTTP_AGENT_HARNESS_DECISION_FAILED' };
      const stands = outcome.sent.decided[a.interactionId] ?? decision;
      const status: PendingApproval['status'] = DECIDED.has(stands) ? (stands as ApprovalDecision) : 'closed';
      return {
        ...a,
        status,
        errorCode: null,
        ...(conversation && stands === decision ? { scope: 'conversation' as const } : {}),
        ...(denyReason && stands === 'denied' ? { denyReason } : {}),
      };
    }),
  );
}
