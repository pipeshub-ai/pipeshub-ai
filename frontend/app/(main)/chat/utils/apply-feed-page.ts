import { useChatStore } from '../store';
import { useParticipantsStore } from '../mentions/participants-store';
import type { ActiveRunDto, FeedPage } from '../collaboration-types';
import type { ChatSlot, ConversationMessage, PendingAskUserQuestion } from '../types';
import { feedToThread } from './feed-rows';
import { maxSeq, mergeMessagesById } from './merge-messages';

function sameRun(a: ActiveRunDto | null, b: ActiveRunDto | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.userId === b.userId && a.startedAt === b.startedAt && a.runId === b.runId;
}

const isResumeQuery = (m: ConversationMessage) =>
  m.messageType === 'user_query' && typeof m.content === 'string' && m.content.trimStart().startsWith('User selections:');

/**
 * What a feed page does to the slot's open question card: another person's card opens (read-only for the
 * viewer, from `requestedBy`), and a card the page shows as answered closes. `undefined` leaves it alone,
 * which includes a card this tab's own stream opened and is still collecting selections for.
 */
function pendingCardAfterFeed(
  current: PendingAskUserQuestion | null,
  opened: PendingAskUserQuestion | null,
  messages: readonly ConversationMessage[],
): PendingAskUserQuestion | null | undefined {
  if (opened && (!current || (current.toolCallMessageId && current.toolCallMessageId !== opened.toolCallMessageId))) {
    return opened;
  }
  if (!opened && current?.toolCallMessageId && messages.some(isResumeQuery)) return null;
  return undefined;
}

/**
 * Merges a feed page into the slot. Writes to the store only when something changed, and ignores a
 * page older than the slot's `rev` (a poll that lost the race with the user's own stream).
 * Returns whether the store was written.
 */
export function applyFeedPage(slotId: string, page: FeedPage): boolean {
  const slot: ChatSlot | undefined = useChatStore.getState().slots[slotId];
  if (!slot) return false;
  // Someone changed who is in the chat (possibly from another tab): the picker must not keep the old list.
  if (slot.convId) useParticipantsStore.getState().noteAclVersion(slot.convId, page.aclVersion);
  if (slot.rev !== null && page.rev < slot.rev) return false;

  const { messages: rows, unansweredAskUserQuestion } = feedToThread(page.messages, page.rev);
  const merged = mergeMessagesById(slot.messages, rows);

  const patch: Partial<ChatSlot> = {};
  if (merged !== slot.messages) patch.messages = merged;
  if (slot.rev !== page.rev) patch.rev = page.rev;
  const card = pendingCardAfterFeed(slot.pendingAskUserQuestion, unansweredAskUserQuestion, page.messages);
  if (card !== undefined) patch.pendingAskUserQuestion = card;
  if (!sameRun(slot.activeRun, page.activeRun ?? null)) patch.activeRun = page.activeRun ?? null;
  if (Object.keys(patch).length === 0) return false;
  useChatStore.getState().updateSlot(slotId, patch);
  return true;
}

/** Highest `seq` to ask the feed for: what the slot holds, or the last page's cursor if that is further. */
export function feedCursor(slotId: string, lastCursor: number): number {
  const messages = useChatStore.getState().slots[slotId]?.messages ?? [];
  return Math.max(maxSeq(messages), lastCursor);
}
