/**
 * Unsent chat text lives in localStorage. It sits in `lib/` rather than the chat
 * feature so the auth store can wipe it on sign-out without importing a feature module.
 */
export const CHAT_DRAFT_KEY_PREFIX = 'pipeshub-chat-draft:';

// Leaving the page after a sign-out flushes the composer one last time; without
// this the text would be written straight back after it was wiped.
let writesBlocked = false;

export function areChatDraftWritesBlocked(): boolean {
  return writesBlocked;
}

/** Call when a new session starts so drafts are kept again. */
export function allowChatDraftWrites(): void {
  writesBlocked = false;
}

/** Removes every saved draft and refuses new ones until the next sign-in. */
export function discardAllChatDrafts(): void {
  writesBlocked = true;
  if (typeof window === 'undefined') return;
  try {
    const storage = window.localStorage;
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (key && key.startsWith(CHAT_DRAFT_KEY_PREFIX)) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
  } catch {
    // storage unavailable: there is nothing saved to remove
  }
}
