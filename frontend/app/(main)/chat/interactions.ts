/**
 * What a reply asks of the person (UXINT): its approvals and its question, as one ordered list anchored to the
 * timeline rows that raised them. The live cards and the saved parts go through the same functions, so a card is
 * drawn at the same row, in the same order, while the run works and after it ends.
 */
import { approvalFromPart } from './durable-run-ui';
import { approvalAskRules } from '@/config';
import { EXTERNAL_POLICY_RULE, isPlanApproval } from './interaction-wording';
import { isAskUserQuestionTool } from './utils/tool-display';
import type { ChatSlot, MessagePart, PendingApproval } from './types';

/** An ask that needs several approvals, an approval counted on it, still waiting for the rest. */
export type CountedApprovals = { counted: number; required: number };

/** The count a decision's answer or the live `approval_counted` frame names, while the call still waits. */
export const countedApprovals = (value: unknown): CountedApprovals | undefined => {
  if (typeof value !== 'object' || value === null) return undefined;
  const { counted, required } = value as Record<string, unknown>;
  return typeof counted === 'number' && typeof required === 'number' && counted < required
    ? { counted, required }
    : undefined;
};

/** One card: an approval, or calls of one batch to the same tool under the same rule (D2). */
export interface ApprovalGroup {
  key: string;
  items: PendingApproval[];
}

/** What names one card across a conversation: interaction ids repeat across runs (UXINT-0). */
export const approvalKey = (a: Pick<PendingApproval, 'runId' | 'childRunId' | 'interactionId'>): string =>
  `${a.runId ?? ''}:${a.childRunId ?? ''}:${a.interactionId}`;

export const isOpenApproval = (a: PendingApproval): boolean => a.status === 'pending' || a.status === 'sending';

export const isOpenGroup = (group: ApprovalGroup): boolean => group.items.some(isOpenApproval);

/**
 * The calls a card still waits on: the N of its "Allow all N". Every "waiting for you" count is in these calls, so
 * the header, the announcement and the card never name different numbers (WP-71), as the server's own count does.
 */
export const openCalls = (group: ApprovalGroup): number => group.items.filter(isOpenApproval).length;

/**
 * The calls "Allow all" may cover together: the same run, child, batch (model turns before it and, WP-113, the asks
 * decided before it: only calls that waited together), tool and rule.
 * A card without its batch (an older server), an unknown effect, an ask of the org's policy webhook (G-40: decided
 * one call at a time) or of an edition rule decided on its own card, a connect pause (G-12), the run's plan (G-04: read on its
 * own plan card), or one already decided when the group formed is its own card.
 */
function batchKey(a: PendingApproval): string | null {
  const ownCard =
    a.reason?.rule === EXTERNAL_POLICY_RULE ||
    (a.reason !== undefined && approvalAskRules.ownCard(a.reason)) ||
    isPlanApproval(a);
  if (a.afterTurns === undefined || a.kind === 'unknown_effect' || a.kind === 'connect' || ownCard) return null;
  return [a.runId ?? '', a.childRunId ?? '', a.afterTurns, a.decidedBefore ?? '', a.toolName, a.reason?.rule ?? ''].join('|');
}

/** Groups same-batch, same-tool, same-rule calls into one card, in the order the cards were asked. */
export function groupApprovals(approvals: readonly PendingApproval[]): ApprovalGroup[] {
  const groups: ApprovalGroup[] = [];
  const byBatch = new Map<string, ApprovalGroup>();
  for (const approval of approvals) {
    const batch = batchKey(approval);
    const group = batch === null ? undefined : byBatch.get(batch);
    if (group) {
      group.items.push(approval);
      continue;
    }
    const created = { key: approvalKey(approval), items: [approval] };
    groups.push(created);
    if (batch !== null) byBatch.set(batch, created);
  }
  return groups;
}

/** The approvals a saved reply records, as cards (receipts once decided). */
export function savedApprovals(parts: readonly MessagePart[] | undefined): PendingApproval[] {
  const out: PendingApproval[] = [];
  for (const part of parts ?? []) {
    const card = approvalFromPart(part);
    if (!card) continue;
    out.push({
      ...card,
      ...(part.runId ? { runId: part.runId } : {}),
      ...(part.seq !== undefined ? { seq: part.seq } : {}),
      ...(part.afterTurns !== undefined ? { afterTurns: part.afterTurns } : {}),
      ...(part.decidedBefore !== undefined ? { decidedBefore: part.decidedBefore } : {}),
      ...(askedAt(part.requestedAt) !== undefined ? { requestedAt: askedAt(part.requestedAt) } : {}),
    });
  }
  return out;
}

/** A journal time as the server wrote it (ISO); anything else is no time. */
function askedAt(value: unknown): string | undefined {
  return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : undefined;
}

/** When the longest-waiting open card was asked (WP-97): the header says "since" it; undefined when none says. */
export function waitingSince(cards: readonly Pick<PendingApproval, 'status' | 'requestedAt'>[]): string | undefined {
  const times = cards
    .filter((card) => card.status === 'pending' || card.status === 'sending')
    .map((card) => askedAt(card.requestedAt))
    .filter((time): time is string => time !== undefined);
  return times.length > 0 ? times.reduce((a, b) => (Date.parse(a) <= Date.parse(b) ? a : b)) : undefined;
}

/**
 * The reply's cards: the saved ones, each replaced by its live copy when the stream has one (the live copy knows
 * a decision just made), then the live ones the saved parts do not have yet.
 */
export function mergeApprovals(
  saved: readonly PendingApproval[],
  live: readonly PendingApproval[],
): PendingApproval[] {
  const liveByKey = new Map(live.map((a) => [approvalKey(a), a]));
  const merged = saved.map((a) => {
    const copy = liveByKey.get(approvalKey(a));
    if (!copy) return a;
    liveByKey.delete(approvalKey(a));
    // The live copy may be an older server's frame without the batch; the saved part has it.
    return {
      ...a,
      ...copy,
      seq: copy.seq ?? a.seq,
      afterTurns: copy.afterTurns ?? a.afterTurns,
      decidedBefore: copy.decidedBefore ?? a.decidedBefore,
    };
  });
  return [...merged, ...liveByKey.values()];
}

/** Where a card sits: under its tool row, or a sub-agent's under that sub-agent's row. */
export function approvalAnchor(a: PendingApproval): string {
  return a.childRunId ? `run:${a.childRunId}` : a.toolCallId;
}

/** The ids a timeline row answers to, for cards anchored on it. */
export function partAnchors(part: MessagePart): string[] {
  if (part.type === 'sub_agent' && part.runId) return [`run:${part.runId}`];
  // A saved approval is drawn by its anchor, never as a row: it anchors nothing (an engine's names no tool row).
  if (part.type === 'approval') return [];
  return part.toolCallId ? [part.toolCallId] : [];
}

/** The row a question card sits under: the call that asked it (the first, as a resume keeps only that one). */
export function questionAnchor(parts: readonly MessagePart[] | undefined): string | null {
  const ask = (parts ?? []).find((part) => part.type === 'tool_call' && isAskUserQuestionTool(part.toolName));
  return ask?.toolCallId ?? null;
}

/** One thing the reply asks, placed: `anchor` null sits after the last row. */
export interface PlacedInteraction {
  key: string;
  anchor: string | null;
  open: boolean;
  /** Tie-break inside one row: journal order, then the order it arrived. */
  seq: number;
}

/**
 * The reply's interactions in the order the run asked them (D1): by the row they sit under, then journal seq.
 * Rows are the timeline's own order, so the screen and this order never disagree.
 */
export function orderInteractions<T extends PlacedInteraction>(
  items: readonly T[],
  rowOf: (anchor: string) => number | undefined,
): T[] {
  const row = (item: T): number => (item.anchor === null ? Number.MAX_SAFE_INTEGER : (rowOf(item.anchor) ?? Number.MAX_SAFE_INTEGER));
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => row(a.item) - row(b.item) || a.item.seq - b.item.seq || a.index - b.index)
    .map(({ item }) => item);
}

/** The first open interaction is the one call to action; the others wait their turn (one active card). */
export function activeInteractionKey(ordered: readonly PlacedInteraction[]): string | null {
  return ordered.find((item) => item.open)?.key ?? null;
}

/**
 * What the person's own chat slot waits on (UXINT-2/5): each open call of the run it streams (`openCalls`), and its
 * open question, answered in one go. The pill above the message box and its placeholder read this.
 */
export function slotWaitingCount(
  slot: Pick<ChatSlot, 'isStreaming' | 'pendingApprovals' | 'pendingAskUserQuestion'> & Partial<Pick<ChatSlot, 'messages'>> | undefined,
): number {
  if (!slot) return 0;
  const approvals = slot.isStreaming ? (slot.pendingApprovals ?? []).filter(isOpenApproval).length : 0;
  return approvals + (questionWaits(slot) ? 1 : 0);
}

/** The slot's question still waits: open, and on the newest reply; a newer turn skipped it (D6). */
export function questionWaits(slot: Partial<Pick<ChatSlot, 'pendingAskUserQuestion' | 'messages'>>): boolean {
  const question = slot.pendingAskUserQuestion;
  if (question?.status !== 'pending') return false;
  const newest = [...(slot.messages ?? [])].reverse().find((row) => row.role === 'assistant');
  return !newest || newest.id === question.assistantMessageId;
}

/** How a card's members were decided, for its one-line receipt. */
export interface ReceiptCounts {
  approved: number;
  conversation: number;
  denied: number;
  /** Closed without a person: the run ended, or the card expired. */
  lapsed: number;
}

export function receiptCounts(group: ApprovalGroup): ReceiptCounts {
  const counts: ReceiptCounts = { approved: 0, conversation: 0, denied: 0, lapsed: 0 };
  for (const item of group.items) {
    if (item.status === 'approved' || item.status === 'answered') {
      if (item.scope === 'conversation') counts.conversation += 1;
      else counts.approved += 1;
    } else if (item.status === 'denied') counts.denied += 1;
    else if (!isOpenApproval(item)) counts.lapsed += 1;
  }
  return counts;
}
