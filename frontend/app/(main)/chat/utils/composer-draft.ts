import { areChatDraftWritesBlocked, CHAT_DRAFT_KEY_PREFIX as KEY_PREFIX } from '@/lib/storage/chat-drafts';

/** A draft nobody came back to for this long is dropped on the next write. */
export const DRAFT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
/** Oldest drafts go first once more than this many are stored. */
export const DRAFT_MAX_COUNT = 50;
/** Longer text is not stored; very long pastes become attachments instead. */
export const DRAFT_MAX_CHARS = 20_000;

interface StoredDraft {
  text: string;
  savedAt: number;
}

interface DraftScope {
  userId: string | null | undefined;
  convId: string | null | undefined;
  agentId?: string | null;
}

/**
 * One key per signed-in user, agent and conversation, so a shared browser never
 * shows one person's draft to another. Null when nobody is signed in yet.
 */
export function buildDraftKey({ userId, convId, agentId }: DraftScope): string | null {
  if (!userId) return null;
  return `${KEY_PREFIX}${userId}:${agentId || '-'}:${convId || 'new'}`;
}

// Storage can be missing or throw (private windows, blocked site data, quota).
function getStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

function parseDraft(raw: string | null): StoredDraft | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (
      value &&
      typeof value === 'object' &&
      typeof (value as StoredDraft).text === 'string' &&
      typeof (value as StoredDraft).savedAt === 'number'
    ) {
      return value as StoredDraft;
    }
  } catch {
    // fall through: unreadable drafts are treated as absent
  }
  return null;
}

export function loadDraft(key: string): string {
  const storage = getStorage();
  if (!storage) return '';
  try {
    const draft = parseDraft(storage.getItem(key));
    if (!draft) return '';
    if (Date.now() - draft.savedAt > DRAFT_MAX_AGE_MS) {
      storage.removeItem(key);
      return '';
    }
    return draft.text;
  } catch {
    return '';
  }
}

export function clearDraft(key: string): void {
  if (areChatDraftWritesBlocked()) return;
  try {
    getStorage()?.removeItem(key);
  } catch {
    // nothing to clean up if storage is unavailable
  }
}

function pruneDrafts(storage: Storage, now: number): void {
  const found: Array<{ key: string; savedAt: number }> = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (!key || !key.startsWith(KEY_PREFIX)) continue;
    const draft = parseDraft(storage.getItem(key));
    found.push({ key, savedAt: draft?.savedAt ?? 0 });
  }
  const stale = found.filter((d) => now - d.savedAt > DRAFT_MAX_AGE_MS);
  for (const d of stale) storage.removeItem(d.key);

  const fresh = found
    .filter((d) => now - d.savedAt <= DRAFT_MAX_AGE_MS)
    .sort((a, b) => a.savedAt - b.savedAt);
  for (const d of fresh.slice(0, Math.max(0, fresh.length - DRAFT_MAX_COUNT))) {
    storage.removeItem(d.key);
  }
}

/** Empty or whitespace-only text removes the draft. Over-long text is left unsaved. */
export function saveDraft(key: string, text: string): void {
  if (areChatDraftWritesBlocked()) return;
  const storage = getStorage();
  if (!storage) return;
  try {
    if (text.trim() === '') {
      storage.removeItem(key);
      return;
    }
    if (text.length > DRAFT_MAX_CHARS) return;
    const now = Date.now();
    const draft: StoredDraft = { text, savedAt: now };
    storage.setItem(key, JSON.stringify(draft));
    pruneDrafts(storage, now);
  } catch {
    // a failed save only means the draft is not kept
  }
}
