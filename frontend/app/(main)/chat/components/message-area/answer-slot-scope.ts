'use client';

import { createContext, useContext } from 'react';
import { useChatStore } from '../../store';

/**
 * The slot an answer belongs to, for an answer drawn outside the active slot's list. Without a
 * provider an answer belongs to the active slot. Answer actions (retry, cards, feedback,
 * regenerate) act on this slot, so they never reach another conversation.
 */
export const AnswerSlotContext = createContext<string | null>(null);

/** The answer's slot: the provided one, else the active slot. */
export function useAnswerSlotId(): string | null {
  const scoped = useContext(AnswerSlotContext);
  return useChatStore((s) => scoped ?? s.activeSlotId);
}

/** For event handlers: the provided slot (`useContext(AnswerSlotContext)`), else the slot active right now. */
export function answerSlotIdNow(scoped: string | null): string | null {
  return scoped ?? useChatStore.getState().activeSlotId;
}
