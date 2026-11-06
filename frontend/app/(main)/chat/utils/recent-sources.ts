import type { MentionNode } from '@/chat/utils/node-mentions';

/** The sources a person last picked with `@`, newest first, kept in this browser only. */

export const RECENT_SOURCES_MAX = 10;

export interface RecentSource extends MentionNode {
  /** When it was picked (ms). */
  at: number;
}

const keyOf = (userId: string) => `chat.mentionRecentSources:${userId}`;

function isRecentSource(value: unknown): value is RecentSource {
  const v = value as Partial<RecentSource> | null;
  return Boolean(
    v &&
      typeof v.id === 'string' &&
      typeof v.name === 'string' &&
      typeof v.nodeType === 'string' &&
      typeof v.at === 'number',
  );
}

/** Newest first. Empty when nothing was kept, or storage cannot be read (a private window, blocked site data). */
export function readRecentSources(userId: string | null | undefined): RecentSource[] {
  if (!userId) return [];
  try {
    const raw = window.localStorage.getItem(keyOf(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isRecentSource).slice(0, RECENT_SOURCES_MAX) : [];
  } catch {
    return [];
  }
}

/** Puts the picked sources first; the oldest past the cap are forgotten. */
export function rememberRecentSources(
  userId: string | null | undefined,
  nodes: readonly MentionNode[],
  now: number = Date.now(),
): RecentSource[] {
  if (!userId || nodes.length === 0) return readRecentSources(userId);
  const picked = nodes.map((node, i) => ({
    id: node.id,
    name: node.name,
    nodeType: node.nodeType,
    connector: node.connector ?? '',
    ...(node.extension ? { extension: node.extension } : {}),
    ...(node.mimeType ? { mimeType: node.mimeType } : {}),
    // The first of several picked at once counts as the latest.
    at: now - i,
  }));
  const ids = new Set(picked.map((node) => node.id));
  const next = [...picked, ...readRecentSources(userId).filter((node) => !ids.has(node.id))].slice(0, RECENT_SOURCES_MAX);
  try {
    window.localStorage.setItem(keyOf(userId), JSON.stringify(next));
  } catch {
    // Nothing kept: the menu still works, in its usual order.
  }
  return next;
}

/** When each source was last picked, by id. */
export function recentSourceTimes(recent: readonly RecentSource[]): Map<string, number> {
  return new Map(recent.map((node) => [node.id, node.at]));
}
