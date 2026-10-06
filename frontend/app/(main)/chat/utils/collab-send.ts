import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore } from '../store';
import { useUserStore } from '@/lib/store/user-store';
import {
  CollaborationApi,
  conversationErrorCode,
  conversationErrorDetails,
  conversationErrorStatus,
} from '../collaboration-api';
import { FEED_NOT_MODIFIED, type ActiveRunDto, type ConversationRef } from '../collaboration-types';
import type { AttachmentRef, ChatSlot, QueuedSend, StreamChatRequest } from '../types';
import type { MentionRef } from '../components/composer/composer-input.types';
import { collabSendEnabled } from './collab-flag';
import { applyFeedPage, feedCursor } from './apply-feed-page';
import { newClientMessageId } from './collab-send-fields';
import { clearDraft, saveDraft } from './draft-storage';
import { conversationErrorMessage, isAccessLostError } from './conversation-errors';
import { i18n } from '@/lib/i18n';
import { toast } from '@/lib/store/toast-store';
import { mergeMessagesById, maxSeq } from './merge-messages';

/** Adds `clientMessageId` and `baseSeq` to a send. Does nothing, and returns null, with the flag off or in a solo chat. */
export function prepareCollabSend(
  slot: Pick<ChatSlot, 'messages' | 'access'>,
  request: StreamChatRequest,
): { clientMessageId: string } | null {
  if (!collabSendEnabled() || slot.access?.isCollaborative !== true) return null;
  request.clientMessageId ??= newClientMessageId();
  const base = maxSeq(slot.messages);
  if (request.baseSeq === undefined && base >= 0) request.baseSeq = base;
  return { clientMessageId: request.clientMessageId };
}

/** Metadata for the sender's own row while the server has not stored it yet. */
export function optimisticRowCustom(clientMessageId: string): Record<string, unknown> {
  const profile = useUserStore.getState().profile;
  return {
    clientMessageId,
    pending: true,
    ...(profile?.userId
      ? { author: { userId: profile.userId, displayName: profile.fullName || null } }
      : {}),
  };
}

export function refOf(slot: Pick<ChatSlot, 'convId' | 'threadAgentId'>): ConversationRef | null {
  if (!slot.convId) return null;
  return slot.threadAgentId
    ? { kind: 'agent', agentKey: slot.threadAgentId, id: slot.convId }
    : { kind: 'chat', id: slot.convId };
}

/** Fetches what the slot is missing and merges it. Failures are left to the next poll. */
export async function refreshFeedForSlot(slotId: string): Promise<void> {
  const slot = useChatStore.getState().slots[slotId];
  const ref = slot && refOf(slot);
  if (!slot || !ref) return;
  try {
    const result = await CollaborationApi.fetchFeed(ref, { afterSeq: feedCursor(slotId, -1), rev: null });
    if (result !== FEED_NOT_MODIFIED) applyFeedPage(slotId, result);
  } catch (error) {
    if (isAccessLostError(error)) useChatStore.getState().updateSlot(slotId, { accessLost: true });
  }
}

function parseActiveRun(value: unknown): ActiveRunDto | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.userId !== 'string') return null;
  return {
    userId: v.userId,
    displayName: typeof v.displayName === 'string' ? v.displayName : '',
    startedAt: typeof v.startedAt === 'string' ? v.startedAt : new Date().toISOString(),
    ...(typeof v.runId === 'string' ? { runId: v.runId } : {}),
  };
}

/**
 * A card answer cannot be queued like a typed message, so a busy chat only records who is running.
 * The card stays answerable and the caller shows the busy message. Returns whether the error was a busy one.
 */
export function adoptBusyRunFromError(slotId: string, error: unknown): boolean {
  if (!collabSendEnabled() || conversationErrorCode(error) !== 'CONVERSATION_BUSY') return false;
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return false;
  const run = parseActiveRun(conversationErrorDetails(error)?.activeRun) ?? slot.activeRun;
  if (run) useChatStore.getState().updateSlot(slotId, { activeRun: run, rev: null });
  return true;
}

export interface RejectedSend {
  /** The slot's messages before the optimistic rows were added. */
  baseMessages: ThreadMessageLike[];
  query: string;
  attachments?: AttachmentRef[];
  mentions?: MentionRef[];
  clientMessageId: string;
}

/**
 * Handles the 409s a collaborative send can get. The optimistic rows are rolled back in all three cases
 * and the message is kept (queued or offered again), never dropped. Returns false for any other error,
 * which the caller shows as before.
 */
export function handleRejectedSend(slotId: string, error: unknown, sent: RejectedSend): boolean {
  if (!collabSendEnabled()) return false;
  const code = conversationErrorCode(error);
  const details = conversationErrorDetails(error);
  const slot = useChatStore.getState().slots[slotId];
  if (!slot) return false;

  const rollback: Partial<ChatSlot> = {
    isStreaming: false,
    streamingContent: '',
    streamingQuestion: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: [],
    pendingCollections: [],
    abortController: null,
    runId: null,
    stopping: false,
    messages: sent.baseMessages,
  };
  const held: QueuedSend = {
    query: sent.query,
    ...(sent.attachments?.length ? { attachments: sent.attachments } : {}),
    ...(sent.mentions?.length ? { mentions: sent.mentions } : {}),
    clientMessageId: sent.clientMessageId,
    queuedAt: Date.now(),
  };
  const { updateSlot } = useChatStore.getState();

  if (code === 'CONVERSATION_BUSY') {
    if (slot.convId) saveDraft(slot.convId, sent.query);
    updateSlot(slotId, {
      ...rollback,
      // Without a parsable run the queue would fire at once; the next poll (rev reset) replaces this stand-in.
      activeRun: parseActiveRun(details?.activeRun) ??
        slot.activeRun ?? { userId: '', displayName: '', startedAt: new Date().toISOString() },
      rev: null,
      queuedSend: held,
    });
    return true;
  }

  if (code === 'CONVERSATION_CHANGED') {
    if (slot.convId) saveDraft(slot.convId, sent.query);
    const count = typeof details?.newerCount === 'number' ? details.newerCount : 0;
    updateSlot(slotId, { ...rollback, changedNotice: { count, pending: held } });
    void refreshFeedForSlot(slotId);
    return true;
  }

  if (code === 'DUPLICATE_MESSAGE') {
    if (slot.convId) clearDraft(slot.convId);
    updateSlot(slotId, { ...rollback, rev: null });
    void refreshFeedForSlot(slotId);
    return true;
  }

  const status = conversationErrorStatus(error);
  if (code && status !== undefined && status >= 400 && status < 500 && status !== 401 && status !== 429) {
    // A coded refusal before the stream started: nothing was stored, so no local-only bubble is left behind.
    // The text goes back to the composer and the reason is shown.
    if (slot.convId) saveDraft(slot.convId, sent.query);
    if (code === 'OWNER_INACTIVE') {
      // The banner says why; the text stays in the draft for when the chat has a new owner.
      updateSlot(slotId, { ...rollback, ownerInactive: true });
      return true;
    }
    updateSlot(slotId, { ...rollback, composerRestore: sent.query });
    toast.error(conversationErrorMessage(i18n.t.bind(i18n), error));
    return true;
  }
  if ((status === 403 || status === 409) && slot.convId) saveDraft(slot.convId, sent.query);
  return false;
}

/**
 * Folds a finished turn into the slot's rows instead of replacing them, so rows other people added
 * and older loaded pages stay. This send's placeholder and optimistic row go; the stored rows replace them.
 */
export function mergeCompletedTurn(
  current: readonly ThreadMessageLike[],
  finalRows: readonly ThreadMessageLike[],
  sent: { pendingAssistantId: string; clientMessageId: string },
): ThreadMessageLike[] {
  const settled = current.filter(
    (row) =>
      row.id !== sent.pendingAssistantId &&
      !(
        (row.metadata?.custom as { clientMessageId?: string } | undefined)?.clientMessageId ===
          sent.clientMessageId && row.role === 'user'
      ),
  );
  return mergeMessagesById(settled, finalRows);
}
