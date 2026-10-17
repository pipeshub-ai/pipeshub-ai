import { useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { clearDraft, loadDraft, saveDraft } from '../utils/composer-draft';

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
  /** Text is not the reader's own (edit or regenerate mode), so it is never saved. */
  paused?: boolean;
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
  paused = false,
}: UseComposerDraftOptions): void {
  const messageRef = useRef(message);
  const pausedRef = useRef(paused);
  // Declared first so every effect below sees this commit's values.
  useEffect(() => {
    messageRef.current = message;
    pausedRef.current = paused;
  });
  // The key the text currently in the box belongs to. It only moves in the
  // effect below, so a text change that lands in the same commit as a thread
  // switch can never be filed under the wrong conversation.
  const ownerKeyRef = useRef<string | null>(null);
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
      if (!pausedRef.current) saveDraft(draftKey, messageRef.current);
      return;
    }

    if (leavingKey && !pausedRef.current) saveDraft(leavingKey, messageRef.current);
    ownerKeyRef.current = draftKey;
    if (!draftKey) return;

    // Read now, not inside the state updater: a strict-mode remount flushes the
    // still-empty box in between, which would otherwise erase the draft first.
    const stored = loadDraft(draftKey);
    if (leavingKey || userChanged) {
      // Another conversation, or another person: show that draft, or nothing.
      setMessage(stored);
    } else {
      // First load, or the account became known: never overwrite text already there.
      setMessage((current) => (current === '' ? stored : current));
    }
  }, [draftKey, userId, slotId, setMessage]);

  useEffect(() => {
    const key = ownerKeyRef.current;
    if (!key || paused) return;
    const timer = setTimeout(() => saveDraft(key, message), DRAFT_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `draftKey` re-arms the timer when the owner changes, so a save queued
    // before a chat got its id cannot land under the new key.
  }, [message, paused, draftKey]);

  useEffect(() => {
    const flush = () => {
      const key = ownerKeyRef.current;
      if (key && !pausedRef.current) saveDraft(key, messageRef.current);
    };
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('pagehide', flush);
      flush();
    };
  }, []);
}
