import { getCurrentUserId } from '@/chat/utils/current-user-id';

/**
 * What the rail showed last time, per person on this device, so the first paint keeps the same room for it: whether
 * the Projects row showed (1 or 0), whether Shared chats offered to start one (1 or 0, so it stays while empty), and
 * how many rows Pinned and Shared chats showed, their More included. Numbers only, never a title.
 */
export type RailMemory = 'projects' | 'sharedStart' | 'pinnedRows' | 'sharedRows';

/** Older than this, what the rail showed has likely changed, so the first paint goes by nothing rather than by it. */
export const RAIL_MEMORY_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

const keyOf = (name: RailMemory) => `pipeshub.rail.${getCurrentUserId() ?? 'signed-out'}.${name}`;

/** The value last remembered for the signed-in person; null when there is none, it is stale, or there is no storage. */
export function readRailMemory(name: RailMemory, now = Date.now()): number | null {
  try {
    const stored = window.localStorage.getItem(keyOf(name));
    if (stored === null) return null;
    const { v, at } = JSON.parse(stored) as { v?: unknown; at?: unknown };
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || typeof at !== 'number') return null;
    return now - at > RAIL_MEMORY_MAX_AGE_MS ? null : v;
  } catch {
    return null;
  }
}

export function writeRailMemory(name: RailMemory, value: number): void {
  try {
    window.localStorage.setItem(keyOf(name), JSON.stringify({ v: value, at: Date.now() }));
  } catch {
    // No storage (private mode, quota): the next first paint just has nothing to go by.
  }
}
