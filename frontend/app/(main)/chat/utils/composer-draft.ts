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
  quotedExcerpt?: string;
  quotedExcerptTruncated?: boolean;
}

export interface DraftQuote {
  excerpt?: string | null;
  truncated?: boolean;
}

export interface DraftFields {
  text: string;
  quotedExcerpt: string | null;
  quotedExcerptTruncated: boolean;
}

const EMPTY_DRAFT: DraftFields = {
  text: '',
  quotedExcerpt: null,
  quotedExcerptTruncated: false,
};

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

export function loadDraftFields(key: string): DraftFields {
  const storage = getStorage();
  if (!storage) return EMPTY_DRAFT;
  try {
    const draft = parseDraft(storage.getItem(key));
    if (!draft) return EMPTY_DRAFT;
    if (Date.now() - draft.savedAt > DRAFT_MAX_AGE_MS) {
      storage.removeItem(key);
      return EMPTY_DRAFT;
    }
    const excerpt =
      typeof draft.quotedExcerpt === 'string' && draft.quotedExcerpt.trim()
        ? draft.quotedExcerpt
        : null;
    return {
      text: draft.text,
      quotedExcerpt: excerpt,
      quotedExcerptTruncated: excerpt ? Boolean(draft.quotedExcerptTruncated) : false,
    };
  } catch {
    return EMPTY_DRAFT;
  }
}

export function loadDraft(key: string): string {
  return loadDraftFields(key).text;
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

/** Empty text and no excerpt removes the draft. Over-long text is left unsaved. */
export function saveDraft(key: string, text: string, quote?: DraftQuote): void {
  if (areChatDraftWritesBlocked()) return;
  const storage = getStorage();
  if (!storage) return;
  try {
    const excerpt = typeof quote?.excerpt === 'string' ? quote.excerpt.trim() : '';
    if (text.trim() === '' && !excerpt) {
      storage.removeItem(key);
      return;
    }
    if (text.length > DRAFT_MAX_CHARS) return;
    const now = Date.now();
    const draft: StoredDraft = {
      text,
      savedAt: now,
      ...(excerpt
        ? { quotedExcerpt: excerpt, quotedExcerptTruncated: Boolean(quote?.truncated) }
        : {}),
    };
    storage.setItem(key, JSON.stringify(draft));
    pruneDrafts(storage, now);
  } catch {
    // a failed save only means the draft is not kept
  }
}
