'use client';

import { createContext, useCallback, useContext, useEffect, type KeyboardEvent, type RefObject } from 'react';
import { create } from 'zustand';

const COMPOSER = 'textarea.chat-input-textarea';

/** The person is typing somewhere, or has text waiting in the message box: a new card must not take focus (D4). */
export function personIsTyping(doc: Document = document): boolean {
  const el = doc.activeElement as HTMLElement | null;
  if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable)) return true;
  const composer = doc.querySelector<HTMLTextAreaElement>(COMPOSER);
  return Boolean(composer?.value.trim());
}

const NO_ASKS: ReadonlyArray<string> = [];

const isControl = (el: EventTarget | null): boolean =>
  el instanceof HTMLElement &&
  (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ||
    el.getAttribute('role') === 'radio' ||
    el.getAttribute('role') === 'checkbox' ||
    el.isContentEditable);

interface AskFocusState {
  /** The ask a link named (`&ask=…`, D9) that no card has taken yet. */
  pending: string | null;
  request: (interactionId: string | null) => void;
}

/**
 * The hand-off from a Needs you link to the card that answers it: the link names the ask, the card holding it takes
 * focus when it renders, however late its conversation loads. A job's card that holds it folded opens on
 * `usePendingAsk`.
 */
export const useAskFocusStore = create<AskFocusState>((set) => ({
  pending: null,
  request: (interactionId) => set({ pending: interactionId || null }),
}));

export const requestAskFocus = (interactionId: string | null): void => useAskFocusStore.getState().request(interactionId);

/** The ask a link asked to focus, while no card has taken it. */
export const usePendingAsk = (): string | null => useAskFocusStore((s) => s.pending);

/** The card holding one of `askIds` takes the pending ask: it is scrolled to and its heading focused, once. */
export function takeAskFocus(askIds: ReadonlyArray<string>, heading: HTMLElement | null): boolean {
  const { pending } = useAskFocusStore.getState();
  if (!pending || !heading || !askIds.includes(pending)) return false;
  useAskFocusStore.setState({ pending: null });
  heading.scrollIntoView?.({ block: 'center' });
  heading.focus({ preventScroll: true });
  return true;
}

/**
 * False inside the card floating over the message box (BGUI-4): an ask landing there is announced, never given
 * focus, since the person may be reading or about to type just below it. A link naming the ask still focuses it.
 */
export const InteractionAutoFocus = createContext(true);

/**
 * The active card's keyboard (UXINT-4): when it becomes the one call to action and the person is not typing,
 * focus moves to its heading. `askIds`: the asks it holds, so a Needs you link naming one lands here. Enter on the
 * card (not on a control) is its primary action; Escape returns to the message box.
 */
export function useInteractionFocus(
  active: boolean,
  headingRef: RefObject<HTMLElement | null>,
  primary: (() => void) | null,
  askIds: ReadonlyArray<string> = NO_ASKS,
): { onKeyDown: (event: KeyboardEvent<HTMLElement>) => void } {
  const pending = usePendingAsk();
  const autoFocus = useContext(InteractionAutoFocus);
  const asks = askIds.join('\n');
  useEffect(() => {
    if (!autoFocus || !active || personIsTyping() || useAskFocusStore.getState().pending) return;
    headingRef.current?.focus({ preventScroll: false });
  }, [autoFocus, active, headingRef]);
  useEffect(() => {
    // A link that named this card's ask is the person's own choice, so it wins over the typing guard.
    if (pending && asks) takeAskFocus(asks.split('\n'), headingRef.current);
  }, [pending, asks, headingRef]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (!active) return;
      if (event.key === 'Escape') {
        const composer = document.querySelector<HTMLTextAreaElement>(COMPOSER);
        if (composer) {
          event.preventDefault();
          composer.focus();
        }
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && primary && !isControl(event.target)) {
        event.preventDefault();
        primary();
      }
    },
    [active, primary],
  );
  return { onKeyDown };
}
