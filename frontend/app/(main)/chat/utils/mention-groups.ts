import type { ReactNode } from 'react';

/**
 * The `@` menu's groups: what each one holds, how many of its rows show before "N more", how rows rank, and the
 * per-message caps checked before a send.
 */

/** Item rows a bare `@` shows in all; past it the largest groups give up rows to their "N more". */
export const MENTION_MENU_MAX_ROWS = 14;
/** Rows a group shows while searching, before its "Show more". */
export const MENTION_SEARCH_GROUP_ROWS = 5;
/** Rows a searched group holds once "Show more" opened it. */
export const MENTION_SEARCH_GROUP_MAX = 20;
/** Sources a bare `@` shows: the apps and the collections each get half. */
export const MENTION_SOURCE_ROWS = 6;
export const MENTION_SOURCE_ROWS_PER_KIND = 3;
/** Longer than any name worth searching for; past it, an `@` is just text. */
export const MENTION_QUERY_MAX = 64;
/** From this many characters the menu asks the server; fewer filter what is loaded. */
export const MENTION_SEARCH_MIN_CHARS = 2;
export const MENTION_SEARCH_DEBOUNCE_MS = 150;
/** Sources one message may name. */
export const MENTION_SOURCES_MAX = 10;

export type MentionGroupStatus = 'ready' | 'loading' | 'error';

/** One pickable row. `value` is what the host gets back when it is picked. */
export interface MentionMenuItem {
  key: string;
  label: string;
  /** Second line, or the text after the name. */
  description?: string;
  /** The row's accessible name, when its text alone does not say it. */
  ariaLabel?: string;
  icon?: ReactNode;
  /** After the text (a mark, a button). */
  trailing?: ReactNode;
  disabled?: boolean;
  /** Always shown, and never moved behind "N more". */
  pinned?: boolean;
  /** Opens a level of its own (a source with things inside). */
  canOpen?: boolean;
  /** When the user last picked or talked to it; recent rows rank first. */
  recentAt?: number;
  /** A handle typed after `@` finds the row too. */
  handle?: string;
  /** A second line that wraps rather than cuts off (a whole sentence). */
  wrapDescription?: boolean;
  value: unknown;
}

export interface MentionGroupView {
  key: string;
  label?: string;
  /** Said after the group's name ("Mentioning shares this chat"). */
  hint?: string;
  items: MentionMenuItem[];
  /** Rows shown before "N more"; the whole list opens in the same box. */
  limit: number;
  status: MentionGroupStatus;
  onRetry?(): void;
  /** The server holds more than `items` (a searched group): "Show more" fetches them. */
  hasMore?: boolean;
  loadMore?(): void;
  /** The "more" row opens something else (all the sources); its label says what. */
  browse?: { label: string; open(): void };
  /** No "N more" row: the group shows `limit` rows and stops (its sibling carries the way on). */
  noMore?: boolean;
}

export type MentionMenuRow =
  | { type: 'item'; groupKey: string; item: MentionMenuItem }
  | { type: 'more'; groupKey: string; count: number | null; browse?: { label: string; open(): void } };

export interface MentionMenuSection {
  group: MentionGroupView;
  rows: MentionMenuRow[];
}

// A name's words: spaces and the separators file and app names use.
const WORD_SPLIT = /[\s_\-./()[\]]+/;

function wordsOf(label: string): string[] {
  return label.toLowerCase().split(WORD_SPLIT).filter(Boolean);
}

/**
 * Whether a row matches what was typed after `@`. One word matches the start of any word of the name, or of the
 * handle; several words are a name typed in order, each starting a word of it ("jo sm" finds "John Michael Smith").
 */
export function mentionMatches(label: string, query: string, handle?: string): boolean {
  const q = query.trim().toLowerCase().replace(/^@/, '');
  if (!q) return true;
  const words = wordsOf(label);
  const tokens = q.split(/\s+/).filter(Boolean);
  if (tokens.length === 1) {
    const [token] = tokens;
    return words.some((word) => word.startsWith(token)) || Boolean(handle && handle.toLowerCase().startsWith(token));
  }
  let at = 0;
  for (const token of tokens) {
    while (at < words.length && !words[at].startsWith(token)) at += 1;
    if (at >= words.length) return false;
    at += 1;
  }
  return true;
}

// 0: the first word or the handle starts with it; 1: another word does; 2: anything else the server matched.
function matchTier(item: MentionMenuItem, query: string): number {
  const q = query.trim().toLowerCase().replace(/^@/, '');
  if (!q) return 0;
  const words = wordsOf(item.label);
  const first = q.split(/\s+/)[0] ?? '';
  if ((words[0] ?? '').startsWith(first) || (item.handle ?? '').toLowerCase().startsWith(first)) return 0;
  if (words.some((word) => word.startsWith(first))) return 1;
  return 2;
}

/**
 * Rows in the order the menu shows them: pinned ones first, then recent ones (latest first), then, for a typed query, those whose first
 * name or handle starts with it, then those with any word starting with it, then the rest alphabetically. With no
 * query the rest keep the order they came in.
 */
export function rankMentionItems(items: readonly MentionMenuItem[], query: string): MentionMenuItem[] {
  const typed = query.trim().length > 0;
  return items
    .map((item, index) => ({ item, index, tier: matchTier(item, query) }))
    .sort((a, b) => {
      if (a.item.pinned !== b.item.pinned) return a.item.pinned ? -1 : 1;
      const ra = a.item.recentAt;
      const rb = b.item.recentAt;
      if (ra !== undefined || rb !== undefined) {
        if (ra === undefined) return 1;
        if (rb === undefined) return -1;
        if (ra !== rb) return rb - ra;
      }
      if (!typed) return a.index - b.index;
      if (a.tier !== b.tier) return a.tier - b.tier;
      const byName = a.item.label.localeCompare(b.item.label, undefined, { sensitivity: 'base', numeric: true });
      return byName !== 0 ? byName : a.index - b.index;
    })
    .map(({ item }) => item);
}

/** The rows of what was typed: those that match, ranked. */
export function filterMentionItems(items: readonly MentionMenuItem[], query: string): MentionMenuItem[] {
  return rankMentionItems(
    items.filter((item) => item.pinned || mentionMatches(item.label, query, item.handle)),
    query,
  );
}

/**
 * What each group shows. Empty groups that have finished are left out. Each shows up to its `limit` and the rest go
 * behind its "N more"; while the shown rows add up to more than `maxRows`, the group showing the most (the later one
 * on a tie) gives one up, never below one. Pinned rows always show. `expanded` shows that one group whole.
 */
export function layoutMentionGroups(
  groups: readonly MentionGroupView[],
  options: { maxRows?: number; expanded?: string | null } = {},
): MentionMenuSection[] {
  const maxRows = options.maxRows ?? MENTION_MENU_MAX_ROWS;
  if (options.expanded) {
    const group = groups.find((g) => g.key === options.expanded);
    if (!group) return [];
    return [{ group, rows: group.items.map((item) => ({ type: 'item' as const, groupKey: group.key, item })) }];
  }
  const live = groups.filter((g) => g.items.length > 0 || g.status !== 'ready' || g.browse);
  const pinned = live.map((g) => g.items.filter((item) => item.pinned));
  const rest = live.map((g) => g.items.filter((item) => !item.pinned));
  const shown = live.map((g, i) => Math.min(rest[i].length, Math.max(0, g.limit - pinned[i].length)));
  const total = () => shown.reduce((sum, n, i) => sum + n + pinned[i].length, 0);
  while (total() > maxRows) {
    let pick = -1;
    for (let i = 0; i < shown.length; i += 1) {
      if (shown[i] > 1 && (pick < 0 || shown[i] >= shown[pick])) pick = i;
    }
    if (pick < 0) break;
    shown[pick] -= 1;
  }
  return live.map((group, i) => {
    const rows: MentionMenuRow[] = [...pinned[i], ...rest[i].slice(0, shown[i])].map((item) => ({
      type: 'item' as const,
      groupKey: group.key,
      item,
    }));
    const hidden = rest[i].length - shown[i];
    if (group.browse) rows.push({ type: 'more', groupKey: group.key, count: null, browse: group.browse });
    else if (!group.noMore && (hidden > 0 || (group.hasMore && group.items.length > 0))) {
      rows.push({ type: 'more', groupKey: group.key, count: group.hasMore ? null : hidden });
    }
    return { group, rows };
  });
}

/** The sources a message names past the per-message cap, or 0. */
export function sourcesOverCap(count: number): number {
  return Math.max(0, count - MENTION_SOURCES_MAX);
}
