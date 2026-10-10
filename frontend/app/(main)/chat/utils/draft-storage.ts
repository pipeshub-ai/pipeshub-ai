const PREFIX = 'ph-conv-draft:';

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** Keeps an unsent message across a rejected send. Never throws: storage can be full or blocked. */
export function saveDraft(conversationId: string, text: string): void {
  if (!conversationId || !text) return;
  try {
    storage()?.setItem(PREFIX + conversationId, text);
  } catch {
    // Send must work without storage.
  }
}

export function loadDraft(conversationId: string): string | null {
  try {
    return storage()?.getItem(PREFIX + conversationId) ?? null;
  } catch {
    return null;
  }
}

export function clearDraft(conversationId: string): void {
  if (!conversationId) return;
  try {
    storage()?.removeItem(PREFIX + conversationId);
  } catch {
    // Nothing to clean up.
  }
}
