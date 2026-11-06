/**
 * Titles renamed in this tab, held over the chat-list reads that may not have them yet. A rename changes the row at
 * once and is never followed by a reload, so a list read that left before the server saved the rename (a quiet
 * refresh on focus, a notice) would otherwise put the old title back, whichever of the two is answered first.
 */
interface Hold {
  title: string;
  /** The last read started before the server confirmed the rename; null while the rename is out. */
  savedAfterRead: number | null;
  savedAt: number;
}

const holds = new Map<string, Hold>();
let reads = 0;

/** Longer than any read can be out (the API's timeout): a saved hold older than this covers no read still out. */
const HOLD_AFTER_SAVE_MS = 120_000;

/** A chat-list read is starting. Pass what this returns to {@link withHeldTitles} with the rows it brings. */
export function startListRead(): number {
  reads += 1;
  return reads;
}

/**
 * Holds the new title of a chat whose rename is out. `saved`: the server confirmed it, so only reads that started
 * before then still need it. `dropped`: refused, and the old title stands again.
 */
export function holdTitle(conversationId: string, title: string): { saved: () => void; dropped: () => void } {
  const hold: Hold = { title, savedAfterRead: null, savedAt: 0 };
  holds.set(conversationId, hold);
  return {
    saved: () => {
      if (holds.get(conversationId) !== hold) return;
      hold.savedAfterRead = reads;
      hold.savedAt = Date.now();
    },
    dropped: () => {
      if (holds.get(conversationId) === hold) holds.delete(conversationId);
    },
  };
}

/**
 * A read's rows, with the titles held over it. A read that started after the rename was saved shows the server's
 * title, which is the new one, or a later rename by someone else.
 */
export function withHeldTitles<T extends { id: string; title: string }>(rows: T[], read: number): T[] {
  return withHeldTitlesBy(rows, read, (row) => row.id);
}

/** {@link withHeldTitles} for rows that carry their chat's id elsewhere (the project rail's `_id`). */
export function withHeldTitlesBy<T extends { title?: string }>(rows: T[], read: number, idOf: (row: T) => string): T[] {
  const now = Date.now();
  for (const [id, hold] of holds) {
    if (hold.savedAfterRead !== null && now - hold.savedAt > HOLD_AFTER_SAVE_MS) holds.delete(id);
  }
  if (holds.size === 0) return rows;
  let changed = false;
  const next = rows.map((row) => {
    const hold = holds.get(idOf(row));
    if (!hold || row.title === hold.title || (hold.savedAfterRead !== null && read > hold.savedAfterRead)) return row;
    changed = true;
    return { ...row, title: hold.title };
  });
  return changed ? next : rows;
}

/** Tests only. */
export function resetHeldTitlesForTests(): void {
  holds.clear();
  reads = 0;
}
