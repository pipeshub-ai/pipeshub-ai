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
  let raw: string | null = null;
  try {
    raw = storage()?.getItem(KEY) ?? null;
  } catch {
    return null;
  }
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
  try {
    storage()?.setItem(KEY, JSON.stringify({ conversationId }));
  } catch {
    // Quota / private-mode: leave helpers must still reach the purge DELETE.
  }
}

export function clearTemporaryChatSession(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // Same as write — do not block leave-time purge.
  }
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

function readPending(): PendingPurge[] {
  let raw: string | null = null;
  try {
    raw = localStore()?.getItem(PENDING_PURGE_KEY) ?? null;
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    // A single object is the shape older builds wrote.
    const list = Array.isArray(parsed) ? parsed : [parsed];
    return list.filter(
      (p): p is PendingPurge =>
        !!p && typeof p.conversationId === 'string' && p.conversationId !== '',
    );
  } catch {
    return [];
  }
}

function writePending(list: PendingPurge[]): void {
  try {
    if (list.length === 0) localStore()?.removeItem(PENDING_PURGE_KEY);
    else localStore()?.setItem(PENDING_PURGE_KEY, JSON.stringify(list));
  } catch {
    // Best-effort marker; missing it only delays cleanup until leave/sweep.
  }
}

/** Remember a temporary chat to delete after the tab is gone (not on F5). One entry per chat, so two tabs do not overwrite each other. */
export function markTemporaryChatPurgePending(conversationId: string): void {
  if (!conversationId) return;
  const rest = readPending().filter((p) => p.conversationId !== conversationId);
  writePending([...rest, { conversationId, ts: Date.now() }]);
}

/** Drops one chat's marker, or every marker when no id is given. */
export function clearTemporaryChatPurgePending(conversationId?: string): void {
  writePending(
    conversationId
      ? readPending().filter((p) => p.conversationId !== conversationId)
      : [],
  );
}

/**
 * Takes every pending marker. A chat whose id this tab's sessionStorage still
 * holds was refreshed — cancel it. The rest belong to closed tabs: return their
 * ids to hard-delete.
 */
export function consumeTemporaryChatPurgePending(): string[] {
  const pending = readPending();
  clearTemporaryChatPurgePending();
  const own = readTemporaryChatSession()?.conversationId;
  return pending.map((p) => p.conversationId).filter((id) => id !== own);
}
