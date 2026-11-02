import { loadConversationIntoSlot } from './load-conversation';
import { usePendingApprovals } from './pending-approvals-store';
import { buildStreamChatRequestForSlot } from './runtime';
import { ctxKeyFromAgent, useChatStore } from './store';
import { streamMessageForSlot } from './streaming';
import { findToolApproval, toolApprovalDetails, toolApprovalResumeQuery } from './tool-approval';
import type { MessagePart, PendingApprovalItem, ToolApprovalAnswer } from './types';
import { fetchModelsForContext } from './utils/fetch-models-for-context';

type Row = { id?: string; role: string; metadata?: { custom?: Record<string, unknown> } };

function rowParts(row: Row): MessagePart[] {
  const parts = row.metadata?.custom?.persistedParts;
  return Array.isArray(parts) ? (parts as MessagePart[]) : [];
}

/** Whether these parts hold the call with this approval id, at any depth, answered or not. */
function holdsApproval(parts: MessagePart[] | undefined, approvalId: string): boolean {
  return (parts ?? []).some(
    (part) =>
      (part.type === 'tool_call' && toolApprovalDetails(part.approval)?.approvalId === approvalId) ||
      holdsApproval(part.parts, approvalId),
  );
}

function rowHolding(slotId: string, approvalId: string): Row | undefined {
  const messages = (useChatStore.getState().slots[slotId]?.messages ?? []) as Row[];
  return messages.find((row) => row.role === 'assistant' && holdsApproval(rowParts(row), approvalId));
}

/** Answers a call waiting in another chat, on that chat's own background slot; the chat on
 * screen stays as it is. Nothing is sent when that chat is still running ('busy') or already
 * shows the card answered ('stale'). */
export async function answerElsewhere(
  item: PendingApprovalItem,
  answer: ToolApprovalAnswer,
): Promise<'sent' | 'busy' | 'stale' | 'failed'> {
  const { approvalId } = item.card;
  let slotId: string;
  try {
    slotId = await loadConversationIntoSlot(item.conversationId, item.agentId);
  } catch {
    return 'failed';
  }
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return 'failed';
  if (slot.isStreaming || slot.remoteRunInProgress) return 'busy';
  const row = rowHolding(slotId, approvalId);
  if (!row?.id) return 'failed';
  if (findToolApproval(rowParts(row))?.approvalId !== approvalId) return 'stale';

  // The chat on screen may be another agent's, whose models are the only ones loaded.
  await fetchModelsForContext(ctxKeyFromAgent(item.agentId)).catch(() => undefined);
  const query = toolApprovalResumeQuery(answer.decision);
  const request = buildStreamChatRequestForSlot(slotId, query, undefined, answer, { agentMode: true });
  if (!request) return 'failed';
  const rowId = row.id;
  void streamMessageForSlot(slotId, query, request, { reuseRowId: rowId }).finally(async () => {
    const after = rowHolding(slotId, approvalId);
    const card = after ? findToolApproval(rowParts(after)) : null;
    await usePendingApprovals.getState().refresh();
    // A refused edit leaves the call waiting with the server's errors; show them where it was answered.
    if (card?.approvalId === approvalId && card.editErrors?.length) {
      usePendingApprovals.getState().put({ ...item, card });
    }
  });
  return 'sent';
}
