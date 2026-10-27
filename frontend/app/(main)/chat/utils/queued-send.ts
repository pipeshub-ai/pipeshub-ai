import i18n from 'i18next';
import { chatExtension } from '@/config';
import { useChatStore } from '@/app/(main)/chat/store';
import type { ChatSlot, QueuedSend, StreamChatRequest } from '@/app/(main)/chat/types';
import { toast } from '@/lib/store/toast-store';

/*
 * The questions queue: a question asked while the chat is busy waits its turn instead of being refused, several of
 * them, first in first out, in this browser tab only (a reload included). The box keeps none of them: the list above
 * it shows them. `chatExtension.queue` says when a chat is busy beyond this tab's own answer, what a queued question
 * carries, and which other slot's queue shares its list and cap.
 */

/** At most this many questions wait per chat, the paired slot's included. */
export const MAX_QUEUED_SENDS = 5;

/** What a send hands the queue; the queue adds its id and time. */
export type QueuedMessage = Omit<QueuedSend, 'clientMessageId' | 'queuedAt' | 'failed'>;

const NO_QUEUE: QueuedSend[] = [];

export function queueOf(slot: Pick<ChatSlot, 'queue'> | undefined): QueuedSend[] {
  return slot?.queue ?? NO_QUEUE;
}

/**
 * A send waits instead of going out now, as `chatExtension.queue.shouldQueue` decides: by default while this tab's
 * answer streams, or behind questions already waiting.
 */
export function shouldQueueSend(slot: ChatSlot | undefined): boolean {
  return slot !== undefined && chatExtension.queue.shouldQueue(slot, queueOf(slot).length > 0);
}

const slotOf = (slotId: string): ChatSlot | undefined => useChatStore.getState().slots[slotId];

/** The slot whose queue is listed and capped with this one's, while it is open. */
export function pairedSlotIdOf(slotId: string): string | null {
  return chatExtension.queue.pairedSlotId(useChatStore.getState().slots, slotId);
}

function queuedWithPair(slotId: string): number {
  const { slots } = useChatStore.getState();
  const pair = chatExtension.queue.pairedSlotId(slots, slotId);
  return queueOf(slots[slotId]).length + (pair ? queueOf(slots[pair]).length : 0);
}

const setQueue = (slotId: string, queue: QueuedSend[]): void => useChatStore.getState().updateSlot(slotId, { queue });

/** Where a text given back goes: the open chat's box, which a paired slot shares, or the slot's own. */
function boxSlotOf(slotId: string): string {
  const { activeSlotId, slots } = useChatStore.getState();
  return activeSlotId && chatExtension.queue.pairedSlotId(slots, slotId) === activeSlotId ? activeSlotId : slotId;
}

function newQueuedId(): string {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** The questions the queue sent, by id: a refusal of one goes back to the queue, never into the box. */
const sentFromQueue = new Set<string>();
/** The request each one went out with, so the stream that refuses it can find it. */
const requestsFromQueue = new WeakMap<StreamChatRequest, QueuedSend>();

export function markSentFromQueue(item: QueuedSend, request?: StreamChatRequest): void {
  sentFromQueue.add(item.clientMessageId);
  if (request) requestsFromQueue.set(request, item);
}

export const wasSentFromQueue = (id: string): boolean => sentFromQueue.has(id);

/**
 * The queued question a refused request carried, with the reason it waits with at the head of its queue; `null` when
 * the request did not come from the queue, or the refusal shows in the thread as for any send.
 */
export function refusedQueuedSend(request: StreamChatRequest, error: unknown): { item: QueuedSend; reason: string } | null {
  const item = requestsFromQueue.get(request);
  if (!item) return null;
  const reason = chatExtension.queue.refusedReason(error);
  return reason ? { item, reason } : null;
}

/**
 * Puts the message at the end of the slot's queue. Past the cap it is refused: its text goes back to the box (unless
 * the box still holds it) and a toast says why. Returns whether it was queued.
 */
export function queueSend(slotId: string, message: QueuedMessage): boolean {
  const slot = slotOf(slotId);
  if (!slot) return false;
  if (queuedWithPair(slotId) >= MAX_QUEUED_SENDS) {
    useChatStore.getState().updateSlot(boxSlotOf(slotId), { composerRestore: message.query });
    toast.error(i18n.t('chat.queue.full', { count: MAX_QUEUED_SENDS }));
    return false;
  }
  const item: QueuedSend = {
    query: message.query,
    ...(message.attachments?.length ? { attachments: message.attachments } : {}),
    ...(message.mentionIds?.length ? { mentionIds: [...message.mentionIds] } : {}),
    ...chatExtension.queue.itemFields(message),
    clientMessageId: newQueuedId(),
    queuedAt: Date.now(),
  };
  setQueue(slotId, [...queueOf(slot), item]);
  return true;
}

/** Takes the first question off the queue to send it; the caller puts it back with `requeueFirst` if it can't go. */
export function takeFirst(slotId: string): QueuedSend | null {
  const [first, ...rest] = queueOf(slotOf(slotId));
  if (!first || first.failed) return null;
  setQueue(slotId, rest);
  return first;
}

/** Back at the head: it could not go yet (it waits again), or it was refused (`failed`, and the queue waits on it). */
export function requeueFirst(slotId: string, item: QueuedSend, failed?: string): void {
  const queue = queueOf(slotOf(slotId)).filter((q) => q.clientMessageId !== item.clientMessageId);
  const back: QueuedSend = { ...item };
  if (failed) back.failed = failed;
  else delete back.failed;
  setQueue(slotId, [back, ...queue]);
}

export function removeQueued(slotId: string, id: string): void {
  const queue = queueOf(slotOf(slotId));
  if (queue.some((q) => q.clientMessageId === id)) setQueue(slotId, queue.filter((q) => q.clientMessageId !== id));
}

/** A refused question tries again when the chat is next free. */
export function retryQueued(slotId: string, id: string): void {
  setQueue(
    slotId,
    queueOf(slotOf(slotId)).map((q) => {
      if (q.clientMessageId !== id) return q;
      const { failed: _failed, ...again } = q;
      return again;
    }),
  );
}

/**
 * Takes the question out of the queue and gives its text back to the box, which the caller has checked is empty. Its
 * files and other fields stay behind.
 */
export function editQueued(slotId: string, id: string): void {
  const slot = slotOf(slotId);
  const item = queueOf(slot).find((q) => q.clientMessageId === id);
  if (!slot || !item) return;
  removeQueued(slotId, id);
  useChatStore.getState().updateSlot(boxSlotOf(slotId), { composerRestore: item.query });
}

export function clearQueue(slotId: string): void {
  if (queueOf(slotOf(slotId)).length > 0) setQueue(slotId, []);
}

/** Texts kept for the box, one paragraph each. */
export const joinKept = (...texts: (string | null | undefined)[]): string =>
  texts.filter((text): text is string => Boolean(text?.trim())).join('\n\n');

/** Deleted or archived: what this tab queued in the chat is dropped, so it never goes out on a return. */
export function dropQueuedFor(conversationId: string): void {
  const { slots } = useChatStore.getState();
  for (const [id, slot] of Object.entries(slots)) {
    const pair = chatExtension.queue.pairedSlotId(slots, id);
    if (slot.convId !== conversationId && (!pair || slots[pair]?.convId !== conversationId)) continue;
    clearQueue(id);
    if (slot.convId) keepHeldSends(slot.convId, []);
  }
  keepHeldSends(conversationId, []);
}

const HELD_PREFIX = 'ph-conv-held:';

function tabStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

/**
 * This tab's copy of a chat's queue: it survives a reload of the tab, and a slot dropped once too many chats are open.
 * Per tab, so another tab never sends it too. Never throws.
 */
export function keepHeldSends(conversationId: string, queue: readonly QueuedSend[]): void {
  try {
    if (queue.length > 0) tabStorage()?.setItem(HELD_PREFIX + conversationId, JSON.stringify(queue));
    else tabStorage()?.removeItem(HELD_PREFIX + conversationId);
  } catch {
    // The queue works without storage, only not past a reload or a dropped slot.
  }
}

const isHeld = (value: unknown): value is QueuedSend =>
  Boolean(value) &&
  typeof (value as QueuedSend).query === 'string' &&
  typeof (value as QueuedSend).clientMessageId === 'string';

/** The tab's copy, oldest first. A copy holding one send rather than a list reads as a queue of one. */
export function heldSendsOf(conversationId: string): QueuedSend[] {
  try {
    const raw = tabStorage()?.getItem(HELD_PREFIX + conversationId);
    const held: unknown = raw ? JSON.parse(raw) : null;
    const list = Array.isArray(held) ? held : held ? [held] : [];
    return list.filter(isHeld).slice(0, MAX_QUEUED_SENDS);
  } catch {
    return [];
  }
}
