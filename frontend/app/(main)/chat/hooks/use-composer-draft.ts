import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { clearDraft, loadDraftFields, saveDraft } from '../utils/composer-draft';

/** How long typing must pause before the draft is written to storage. */
export const DRAFT_SAVE_DEBOUNCE_MS = 400;

interface UseComposerDraftOptions {
  /** Storage key for the open conversation; null turns drafts off. */
  draftKey: string | null;
  /** Who is signed in. Text never moves from one user to another. */
  userId?: string | null;
  /** Identifies the thread; a new id with the same text means the reader changed thread. */
  slotId: string | null;
  message: string;
  setMessage: Dispatch<SetStateAction<string>>;
  quotedExcerpt?: string | null;
  setQuotedExcerpt?: Dispatch<SetStateAction<string | null>>;
  quotedExcerptTruncated?: boolean;
  setQuotedExcerptTruncated?: Dispatch<SetStateAction<boolean>>;
  /** Text is not the reader's own (edit or regenerate mode), so it is never saved. */
  paused?: boolean;
}

interface ComposerDraftControls {
  /** The text in the box has just been sent: drop its draft and stop saving it. */
  markSent: () => void;
  /** The send did not go through: keep `text` as the draft again. */
  unmarkSent: (text: string) => void;
}

/**
 * Keeps what the reader has typed in the composer per conversation: restored
 * on return, saved shortly after typing stops, and flushed when they leave.
 */
export function useComposerDraft({
  draftKey,
  userId = null,
  slotId,
  message,
  setMessage,
  quotedExcerpt = null,
  setQuotedExcerpt,
  quotedExcerptTruncated = false,
  setQuotedExcerptTruncated,
  paused = false,
}: UseComposerDraftOptions): ComposerDraftControls {
  const messageRef = useRef(message);
  const quoteRef = useRef(quotedExcerpt);
  const quoteTruncRef = useRef(quotedExcerptTruncated);
  const pausedRef = useRef(paused);
  // Declared first so every effect below sees this commit's values.
  useEffect(() => {
    messageRef.current = message;
    quoteRef.current = quotedExcerpt;
    quoteTruncRef.current = quotedExcerptTruncated;
    pausedRef.current = paused;
  });
  const persist = useCallback((key: string, text: string) => {
    saveDraft(key, text, { excerpt: quoteRef.current, truncated: quoteTruncRef.current });
  }, []);
  // The key the text currently in the box belongs to. It only moves in the
  // effect below, so a text change that lands in the same commit as a thread
  // switch can never be filed under the wrong conversation.
  const ownerKeyRef = useRef<string | null>(null);
  // Set from the moment a message is sent until the reader types again. The box
  // still holds the sent text for a while, and the first message of a new chat
  // moves the composer to another place in the page, so the old composer is
  // unmounted in that window. A ref, not state, so its last save already sees it.
  const sentRef = useRef(false);
  const canSave = useCallback(() => !pausedRef.current && !sentRef.current, []);
  const lastRef = useRef<{ key: string | null; slotId: string | null } | null>(null);
  // The last user seen, kept across a signed-out gap so A -> signed out -> B is
  // still a change of person.
  const knownUserRef = useRef<string | null>(null);

  useEffect(() => {
    const last = lastRef.current;
    lastRef.current = { key: draftKey, slotId };
    if (last && last.key === draftKey) return;

    const leavingKey = last?.key ?? null;
    const sameThread = last !== null && last.slotId === slotId;
    const userChanged = Boolean(userId && knownUserRef.current && knownUserRef.current !== userId);
    if (userId) knownUserRef.current = userId;

    if (leavingKey && draftKey && sameThread && !userChanged) {
      // A new chat just got its real id: the text in the box moves with it.
      clearDraft(leavingKey);
      ownerKeyRef.current = draftKey;
      if (canSave()) persist(draftKey, messageRef.current);
      return;
    }

    if (leavingKey && canSave()) persist(leavingKey, messageRef.current);
    ownerKeyRef.current = draftKey;
    if (!draftKey) return;

    // Read now, not inside the state updater: a strict-mode remount flushes the
    // still-empty box in between, which would otherwise erase the draft first.
    const stored = loadDraftFields(draftKey);
    if (leavingKey || userChanged) {
      // Another conversation, or another person: show that draft, or nothing.
      setMessage(stored.text);
      setQuotedExcerpt?.(stored.quotedExcerpt);
      setQuotedExcerptTruncated?.(stored.quotedExcerptTruncated);
    } else {
      // First load, or the account became known: never overwrite text already there.
      setMessage((current) => (current === '' ? stored.text : current));
      setQuotedExcerpt?.((current) => current ?? stored.quotedExcerpt);
      setQuotedExcerptTruncated?.((current) => current || stored.quotedExcerptTruncated);
    }
  }, [draftKey, userId, slotId, setMessage, setQuotedExcerpt, setQuotedExcerptTruncated, canSave, persist]);

  useEffect(() => {
    if (message !== '' || quotedExcerpt) sentRef.current = false;
  }, [message, quotedExcerpt]);

  useEffect(() => {
    const key = ownerKeyRef.current;
    if (!key || paused) return;
    const timer = setTimeout(() => {
      if (!sentRef.current) persist(key, message);
    }, DRAFT_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `draftKey` re-arms the timer when the owner changes, so a save queued
    // before a chat got its id cannot land under the new key.
  }, [message, quotedExcerpt, quotedExcerptTruncated, paused, draftKey, persist]);

  useEffect(() => {
    const flush = () => {
      const key = ownerKeyRef.current;
      if (key && canSave()) persist(key, messageRef.current);
    };
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('pagehide', flush);
      flush();
    };
  }, [canSave, persist]);

  const markSent = useCallback(() => {
    sentRef.current = true;
    const key = ownerKeyRef.current;
    if (key) clearDraft(key);
  }, []);

  const unmarkSent = useCallback((text: string) => {
    sentRef.current = false;
    const key = ownerKeyRef.current;
    if (key && !pausedRef.current) persist(key, text);
  }, [persist]);

  return { markSent, unmarkSent };
}
