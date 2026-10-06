import type { Conversation } from '../types';

interface SlotLike {
  convId: string | null;
  accessLost?: boolean;
}

/**
 * Drops chats whose open slot reported lost access (a poll answered 404/403). History stays in
 * the slot; the row leaves the lists. A no-op with the flag off, and returns the same array
 * when nothing is dropped.
 */
export function hideAccessLost<T extends Pick<Conversation, 'id'>>(
  list: T[],
  slots: Record<string, SlotLike>,
  enabled: boolean,
): T[] {
  if (!enabled) return list;
  let lost: Set<string> | null = null;
  for (const slot of Object.values(slots)) {
    if (slot.accessLost && slot.convId) (lost ??= new Set()).add(slot.convId);
  }
  if (!lost) return list;
  const hidden = lost;
  return list.filter((c) => !hidden.has(c.id));
}
