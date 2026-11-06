/**
 * Tab-scoped handle for a temporary chat. sessionStorage survives refresh
 * and is cleared when the tab closes, matching ChatGPT / Claude / Gemini:
 * stay in the thread on F5, do not put it in history.
 *
 * Tab close cannot be told apart from refresh during `pagehide` (both fire).
 * So close only marks a localStorage pending purge; the next app load deletes
 * when sessionStorage no longer has that id (tab was closed). F5 keeps
 * sessionStorage, cancels the pending purge, and restores the thread.
 */
const KEY = 'pipeshub-temporary-chat';
const PENDING_PURGE_KEY = 'pipeshub-temporary-chat-pending-purge';

export type TemporaryChatSession = {
  conversationId: string | null;
};

type PendingPurge = {
  conversationId: string;
  ts: number;
};

function storage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

function localStore(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function readTemporaryChatSession(): TemporaryChatSession | null {
  const raw = storage()?.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as TemporaryChatSession;
    if (!parsed || typeof parsed !== 'object') return null;
    const conversationId =
      typeof parsed.conversationId === 'string' && parsed.conversationId
        ? parsed.conversationId
        : null;
    return { conversationId };
  } catch {
    return null;
  }
}

export function writeTemporaryChatSession(conversationId: string | null): void {
  storage()?.setItem(KEY, JSON.stringify({ conversationId }));
}

export function clearTemporaryChatSession(): void {
  storage()?.removeItem(KEY);
}

/** Conversation id to hard-delete when leaving a temporary chat, or null to keep it. */
export function temporaryChatIdToPurgeOnNavigate(opts: {
  leavingIsTemporary: boolean;
  leavingConvId: string | null | undefined;
  conversationId: string | null | undefined;
  scopedNewChat: boolean;
}): string | null {
  if (!opts.leavingIsTemporary || !opts.leavingConvId) return null;
  if (opts.conversationId && opts.leavingConvId !== opts.conversationId) {
    return opts.leavingConvId;
  }
  // Temporary chats have no conversationId in the URL. Agent/project new-chat
  // is still a leave. F5 on /chat/ is not — that restores from sessionStorage.
  if (!opts.conversationId && opts.scopedNewChat) return opts.leavingConvId;
  return null;
}

/** Remember a temporary chat to delete after the tab is gone (not on F5). */
export function markTemporaryChatPurgePending(conversationId: string): void {
  if (!conversationId) return;
  localStore()?.setItem(
    PENDING_PURGE_KEY,
    JSON.stringify({ conversationId, ts: Date.now() } satisfies PendingPurge),
  );
}

export function clearTemporaryChatPurgePending(): void {
  localStore()?.removeItem(PENDING_PURGE_KEY);
}

/**
 * If the tab refreshed, sessionStorage still holds the id — cancel.
 * If the tab closed, sessionStorage is empty — return the id to hard-delete.
 */
export function consumeTemporaryChatPurgePending(): string | null {
  const raw = localStore()?.getItem(PENDING_PURGE_KEY);
  if (!raw) return null;
  let pending: PendingPurge | null = null;
  try {
    pending = JSON.parse(raw) as PendingPurge;
  } catch {
    clearTemporaryChatPurgePending();
    return null;
  }
  if (!pending || typeof pending.conversationId !== 'string' || !pending.conversationId) {
    clearTemporaryChatPurgePending();
    return null;
  }
  const session = readTemporaryChatSession();
  if (session?.conversationId === pending.conversationId) {
    clearTemporaryChatPurgePending();
    return null;
  }
  clearTemporaryChatPurgePending();
  return pending.conversationId;
}
