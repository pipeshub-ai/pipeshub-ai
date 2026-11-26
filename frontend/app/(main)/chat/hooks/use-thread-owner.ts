'use client';

import { createContext, useContext } from 'react';
import { useChatStore } from '../store';

/**
 * The conversation whose messages the assistant-ui thread holds. The chat page hands it to the runtime as the
 * adapter's `extras`, so the thread's messages and their owner change in the same snapshot.
 */
export interface ThreadOwner {
  slotId: string | null;
  convId: string | null;
}

export function threadOwnerOf(extras: unknown): ThreadOwner | null {
  if (typeof extras !== 'object' || extras === null) return null;
  const { slotId, convId } = extras as Partial<ThreadOwner>;
  if (slotId !== null && typeof slotId !== 'string') return null;
  if (convId !== null && typeof convId !== 'string') return null;
  return { slotId, convId };
}

/**
 * Provided by the message list from the thread it draws. Undefined outside a thread (the activity panel, a job's
 * panel), where every value already comes from the chat store alone.
 */
export const ThreadOwnerContext = createContext<ThreadOwner | null | undefined>(undefined);

/**
 * False while the thread drawn is not yet the active conversation's. The runtime takes a switched-to chat's messages
 * in an effect after the render that switched, so for that one render the previous chat's answers are drawn under the
 * new chat's id: a run of the chat just left read through the new chat's routes, which answer "Agent run not found".
 */
export function useThreadIsActive(): boolean {
  const owner = useContext(ThreadOwnerContext);
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  if (owner === undefined) return true;
  return owner !== null && owner.slotId === activeSlotId && owner.convId === convId;
}
