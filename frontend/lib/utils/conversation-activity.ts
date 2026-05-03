import type { Conversation } from '@/chat/types';

/**
 * Epoch ms for "last activity" used for ordering and time buckets.
 * - Prefers numeric `lastActivityAt` from GET /conversations; values < 1e12 are treated as **seconds** (s→×1000), otherwise ms.
 * - Falls back to `updatedAt` then `createdAt` (ISO strings).
 */
export function getConversationLastActivityMs(conv: Conversation): number {
  const raw = conv.lastActivityAt;
  if (raw != null) {
    const n = Number(raw);
    if (!Number.isNaN(n) && n !== 0) {
      if (n < 1_000_000_000_000) return n * 1000;
      return n;
    }
  }
  const u = Date.parse(conv.updatedAt);
  if (!Number.isNaN(u)) return u;
  if (conv.createdAt) {
    const c = Date.parse(conv.createdAt);
    if (!Number.isNaN(c)) return c;
  }
  return 0;
}
