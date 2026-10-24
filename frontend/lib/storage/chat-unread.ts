/**
 * Which chats have an answer the user has not opened yet, kept in localStorage so
 * the sidebar dot survives a reload. It sits in `lib/` rather than the chat
 * feature so the auth store can wipe it on sign-out without importing a feature module.
 */
export const CHAT_UNREAD_KEY_PREFIX = 'pipeshub-chat-unread:';

/** A chat nobody opened for this long stops being flagged on the next write. */
export const UNREAD_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
/** Oldest flags go first once more than this many chats are stored. */
export const UNREAD_MAX_COUNT = 100;

// A run that finishes after a deliberate sign-out must not write its flag back.
let writesBlocked = false;

/** Call when a new session starts so unread chats are remembered again. */
export function allowChatUnreadWrites(): void {
  writesBlocked = false;
}

/** One key per signed-in user, so a shared browser never shows one person's dots to another. */
export function chatUnreadKey(userId: string | null | undefined): string | null {
  return userId ? `${CHAT_UNREAD_KEY_PREFIX}${userId}` : null;
}

// Storage can be missing or throw (private windows, blocked site data, quota).
function getStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** Conversation id -> when it was flagged. Anything unreadable is treated as empty. */
function readFlags(storage: Storage, key: string): Record<string, number> {
  try {
    const value: unknown = JSON.parse(storage.getItem(key) ?? 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const flags: Record<string, number> = {};
    for (const [id, savedAt] of Object.entries(value as Record<string, unknown>)) {
      if (typeof savedAt === 'number' && Number.isFinite(savedAt)) flags[id] = savedAt;
    }
    return flags;
  } catch {
    return {};
  }
}

function freshFlags(flags: Record<string, number>, now: number): Array<[string, number]> {
  return Object.entries(flags)
    .filter(([, savedAt]) => now - savedAt <= UNREAD_MAX_AGE_MS)
    .sort((a, b) => a[1] - b[1]);
}

function writeFlags(storage: Storage, key: string, flags: Array<[string, number]>): void {
  if (flags.length === 0) {
    storage.removeItem(key);
    return;
  }
  storage.setItem(key, JSON.stringify(Object.fromEntries(flags)));
}

/** The chats this user still has an unread answer in. */
export function loadUnreadConversationIds(userId: string | null | undefined): Set<string> {
  const key = chatUnreadKey(userId);
  const storage = getStorage();
  if (!key || !storage) return new Set();
  try {
    return new Set(freshFlags(readFlags(storage, key), Date.now()).map(([id]) => id));
  } catch {
    return new Set();
  }
}

// Each change re-reads storage first, so a second tab's change is not undone by this one.
function updateFlags(
  userId: string | null | undefined,
  change: (flags: Record<string, number>, now: number) => void,
): void {
  if (writesBlocked) return;
  const key = chatUnreadKey(userId);
  const storage = getStorage();
  if (!key || !storage) return;
  try {
    const now = Date.now();
    const flags = readFlags(storage, key);
    change(flags, now);
    const kept = freshFlags(flags, now);
    writeFlags(storage, key, kept.slice(Math.max(0, kept.length - UNREAD_MAX_COUNT)));
  } catch {
    // a failed save only means the dot is not kept across a reload
  }
}

export function rememberUnreadConversation(userId: string | null | undefined, conversationId: string): void {
  updateFlags(userId, (flags, now) => {
    flags[conversationId] = now;
  });
}

export function forgetUnreadConversation(userId: string | null | undefined, conversationId: string): void {
  updateFlags(userId, (flags) => {
    delete flags[conversationId];
  });
}

/** Removes every saved flag and refuses new ones until the next sign-in. */
export function discardAllChatUnread(): void {
  writesBlocked = true;
  const storage = getStorage();
  if (!storage) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (key && key.startsWith(CHAT_UNREAD_KEY_PREFIX)) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
  } catch {
    // storage unavailable: there is nothing saved to remove
  }
}
