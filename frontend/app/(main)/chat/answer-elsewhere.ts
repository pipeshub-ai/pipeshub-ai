import { fetchPendingApprovals } from './api';
import { loadConversationIntoSlot } from './load-conversation';
import { usePendingApprovals } from './pending-approvals-store';
import { buildStreamChatRequestForSlot } from './runtime';
import { ctxKeyFromAgent, useChatStore } from './store';
import { streamMessageForSlot } from './streaming';
import { toolApprovalDetails, toolApprovalResumeQuery } from './tool-approval';
import type { MessagePart, PendingApprovalItem, ToolApprovalAnswer, ToolApprovalDetails } from './types';
import { fetchModelsForContext } from './utils/fetch-models-for-context';

type Row = { id?: string; role: string; metadata?: { custom?: Record<string, unknown> } };

/** `refused`: the server sent the card back with the edit's errors; it is back in the list. */
export type AnswerElsewhereResult = 'sent' | 'busy' | 'stale' | 'failed' | 'refused';

function rowParts(row: Row): MessagePart[] {
  const parts = row.metadata?.custom?.persistedParts;
  return Array.isArray(parts) ? (parts as MessagePart[]) : [];
}

/** The part holding the call with this approval id, at any depth, answered or not. */
function approvalPart(parts: MessagePart[] | undefined, approvalId: string): MessagePart | null {
  for (const part of parts ?? []) {
    if (part.type === 'tool_call' && toolApprovalDetails(part.approval)?.approvalId === approvalId) return part;
    const inner = approvalPart(part.parts, approvalId);
    if (inner) return inner;
  }
  return null;
}

function rowHolding(slotId: string, approvalId: string): Row | undefined {
  const messages = (useChatStore.getState().slots[slotId]?.messages ?? []) as Row[];
  return messages.find((row) => row.role === 'assistant' && approvalPart(rowParts(row), approvalId));
}

const waiting = (part: MessagePart | null) => part?.status === 'awaiting_approval' && !part.approvalAnswer;

/** The slot is ready to answer in: not running, and its saved reply holds the call. */
function ready(slotId: string, approvalId: string): 'ok' | 'busy' | 'missing' {
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return 'missing';
  if (slot.isStreaming || slot.remoteRunInProgress) return 'busy';
  return rowHolding(slotId, approvalId)?.id ? 'ok' : 'missing';
}

/** Answers a call waiting in another chat, on that chat's own background slot; the chat on
 * screen stays as it is. Resolves once the server took the answer ('sent'), sent the card back
 * with an edit's errors ('refused'), or the stream failed ('failed'). Nothing is sent when that
 * chat is still running ('busy') or the call was already answered ('stale'). */
export async function answerElsewhere(item: PendingApprovalItem, answer: ToolApprovalAnswer): Promise<AnswerElsewhereResult> {
  const { approvalId } = item.card;
  let slotId: string;
  try {
    slotId = await loadConversationIntoSlot(item.conversationId, item.agentId);
    let state = ready(slotId, approvalId);
    // A slot loaded before this call was asked, or one whose run has since ended, is out of date.
    if (state === 'missing' || (state === 'busy' && !useChatStore.getState().slots[slotId]?.isStreaming)) {
      slotId = await loadConversationIntoSlot(item.conversationId, item.agentId, { refetch: true });
      state = ready(slotId, approvalId);
    }
    if (state !== 'ok') return state === 'busy' ? 'busy' : 'failed';
  } catch {
    return 'failed';
  }
  const row = rowHolding(slotId, approvalId) as Row & { id: string };
  if (!waiting(approvalPart(rowParts(row), approvalId))) return 'stale';
  // Answered elsewhere (another tab or device) since the list was fetched.
  const stillPending = await fetchPendingApprovals().then(
    (items) => items.some((i) => i.card.approvalId === approvalId),
    () => true,
  );
  if (!stillPending) return 'stale';

  // The chat on screen may be another agent's, whose models are the only ones loaded.
  await fetchModelsForContext(ctxKeyFromAgent(item.agentId)).catch(() => undefined);
  const query = toolApprovalResumeQuery(answer.decision);
  const request = buildStreamChatRequestForSlot(slotId, query, undefined, answer, { agentMode: true, background: true });
  if (!request) return 'failed';

  return new Promise<AnswerElsewhereResult>((resolve) => {
    let done = false;
    const finish = (result: AnswerElsewhereResult) => {
      if (done) return;
      done = true;
      unsubscribe();
      resolve(result);
    };
    // The call settling in the live reply means the server took the answer.
    const unsubscribe = useChatStore.subscribe((state) => {
      const live = approvalPart(state.slots[slotId]?.streamingParts, approvalId);
      if (live && !waiting(live)) finish('sent');
    });
    void streamMessageForSlot(slotId, query, request, { reuseRowId: row.id })
      .catch(() => undefined)
      .then(async () => {
        const after = rowHolding(slotId, approvalId);
        const part = after ? approvalPart(rowParts(after), approvalId) : null;
        const card = part ? toolApprovalDetails(part.approval) : null;
        await usePendingApprovals.getState().refresh();
        if (!waiting(part)) return finish('sent');
        if (card?.editErrors?.length) {
          usePendingApprovals.getState().put({ ...item, card: card as ToolApprovalDetails });
          return finish('refused');
        }
        // The stream failed (it said why in a toast); the call still waits.
        finish('failed');
      });
  });
}
