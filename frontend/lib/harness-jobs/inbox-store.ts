'use client';

import { create } from 'zustand';
import type { InboxCounts } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-counts';
import type { InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import type { InboxPreferences } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-preferences';
import type { InboxFilter, InboxView, PauseChoice } from './inbox';
import { InboxApi, personTimeZone, type NoticeAction, type NoticeSelection } from './inbox-api';
import { refreshJobLists } from './swr-keys';

/**
 * The Inbox's state, above every page (one panel, opened from the rail's glyph, the phone bar and the palette's
 * Inbox command). The count is the server's (`GET …/inbox/count`, pushed as `inboxCount`); the list is read when the
 * panel opens and after each change the person makes, never kept as a second copy of the record. A push is a hint;
 * the read is the record (ADR-INBOX-01 D7).
 */

export const EMPTY_COUNTS: InboxCounts = { unread: 0, waiting: 0, badge: 0, paused: false, pausedUntil: null };

interface InboxState {
  open: boolean;
  counts: InboxCounts;
  /** A count has been read since sign-in: until then a change of `waiting` is not news. */
  countsKnown: boolean;
  filter: InboxFilter;
  view: InboxView;
  items: InboxItem[];
  cursor: string | null;
  loading: boolean;
  loadingMore: boolean;
  failed: boolean;
  /** Sources that did not answer this time: their rows are missing, never guessed. */
  partial: string[];
  prefs: InboxPreferences | null;
  /** Bumped by every read, so a slow read that lost the race is dropped. */
  seq: number;
  setOpen: (open: boolean) => void;
  setCounts: (counts: InboxCounts) => void;
  setFilter: (filter: InboxFilter) => void;
  setView: (view: InboxView) => void;
}

export const useInboxStore = create<InboxState>()((set) => ({
  open: false,
  counts: EMPTY_COUNTS,
  countsKnown: false,
  filter: 'all',
  view: 'inbox',
  items: [],
  cursor: null,
  loading: false,
  loadingMore: false,
  failed: false,
  partial: [],
  prefs: null,
  seq: 0,
  setOpen: (open) => set({ open }),
  setCounts: (counts) => set({ counts }),
  setFilter: (filter) => set({ filter }),
  setView: (view) => set({ view }),
}));

export function openInbox(): void {
  useInboxStore.getState().setOpen(true);
  void reloadInbox();
  void loadPreferences();
}

export function closeInbox(): void {
  useInboxStore.getState().setOpen(false);
}

/** The Needs you page: the asks only (`filter=waiting`), kept fresh by pushes while it is open. */
export function openNeedsYou(): void {
  useInboxStore.setState({ open: true, filter: 'waiting', view: 'inbox' });
  void reloadInbox();
}

/** The server's count of asks waiting on the person (D7): the badge, the menu dot and the page's tab. */
export const useNeedsYouCount = (): number => useInboxStore((s) => s.counts.waiting);

/** The person's jobs at work (CJ14-FU-1), for the sidebar's Jobs entry; 0 when the server sent no `working`. */
export const useWorkingJobsCount = (): number => useInboxStore((s) => s.counts.working ?? 0);

/**
 * The server's counts, from a push or a read. A change of `waiting` means an ask came or went somewhere: the
 * sidebar's dots and the Jobs list are read again, and an open Needs you list too (its row drops at the push, never
 * before: the server decides whether an answer settled it).
 */
export function takeCounts(counts: InboxCounts): void {
  const { counts: before, countsKnown, open } = useInboxStore.getState();
  useInboxStore.setState({ counts, countsKnown: true });
  if (!countsKnown || before.waiting === counts.waiting) return;
  refreshJobLists();
  if (open) void reloadInbox();
}

export async function refreshCount(): Promise<void> {
  try {
    takeCounts(await InboxApi.count());
  } catch {
    // The harness may be off for this org: the glyph stays quiet.
  }
}

/** Reads the first page for the filter and view showing. */
export async function reloadInbox(): Promise<void> {
  const { filter, view } = useInboxStore.getState();
  const seq = useInboxStore.getState().seq + 1;
  useInboxStore.setState({ seq, loading: true, failed: false });
  try {
    const feed = await InboxApi.feed({ filter, view });
    if (useInboxStore.getState().seq !== seq) return;
    useInboxStore.setState({ items: feed.items, cursor: feed.cursor ?? null, counts: feed.counts, partial: feed.partial ?? [], loading: false });
  } catch {
    if (useInboxStore.getState().seq !== seq) return;
    useInboxStore.setState({ loading: false, failed: true });
  }
}

export async function loadMoreInbox(): Promise<void> {
  const { cursor, filter, view, loadingMore, seq } = useInboxStore.getState();
  if (!cursor || loadingMore) return;
  useInboxStore.setState({ loadingMore: true });
  try {
    const feed = await InboxApi.feed({ filter, view, cursor });
    if (useInboxStore.getState().seq !== seq) return;
    const seen = new Set(useInboxStore.getState().items.map((i) => i.id));
    useInboxStore.setState((s) => ({ items: [...s.items, ...feed.items.filter((i) => !seen.has(i.id))], cursor: feed.cursor ?? null, counts: feed.counts }));
  } catch {
    // The rows already shown stay; the next scroll tries again.
  } finally {
    useInboxStore.setState({ loadingMore: false });
  }
}

export function setInboxFilter(filter: InboxFilter): void {
  useInboxStore.getState().setFilter(filter);
  void reloadInbox();
}

export function setInboxView(view: InboxView): void {
  useInboxStore.setState({ view, filter: 'all' });
  void reloadInbox();
}

/**
 * One action on notices, shown at once and then read back. Returns the ids it changed (for an Undo), or null when
 * the server refused it, in which case the list is read again so nothing claims what did not happen.
 */
export async function applyNotices(action: NoticeAction, selection: NoticeSelection): Promise<string[] | null> {
  const ids = 'ids' in selection ? new Set(selection.ids) : null;
  const all = 'all' in selection;
  useInboxStore.setState((s) => ({
    items: s.items.flatMap((item) => {
      if (item.waiting || !(all || ids?.has(item.id))) return [item];
      if (action === 'read') return [{ ...item, unread: false }];
      if (action === 'unread') return [{ ...item, unread: true }];
      // Archive and unarchive move the row to the other view.
      return [];
    }),
  }));
  try {
    const result = await InboxApi.notices(action, selection);
    useInboxStore.getState().setCounts(result.counts);
    if (action !== 'read') void reloadInbox();
    return result.changed;
  } catch {
    void reloadInbox();
    return null;
  }
}

export async function loadPreferences(): Promise<void> {
  try {
    useInboxStore.setState({ prefs: await InboxApi.preferences() });
  } catch {
    // Without preferences, Pause and the channel switches are not offered.
  }
}

export async function setPause(choice: PauseChoice): Promise<boolean> {
  try {
    const timeZone = choice === 'tomorrow' ? personTimeZone() : undefined;
    const prefs = await InboxApi.updatePreferences({ pause: { choice, ...(timeZone ? { timeZone } : {}) } });
    useInboxStore.setState({ prefs });
    await refreshCount();
    return true;
  } catch {
    return false;
  }
}

export async function setWorkflowMuted(scheduleId: string, muted: boolean): Promise<boolean> {
  const mark = (on: boolean) =>
    useInboxStore.setState((s) => ({
      items: s.items.map((i) => (i.pointer.type === 'schedule' && i.pointer.scheduleId === scheduleId ? { ...i, muted: on } : i)),
    }));
  mark(muted);
  try {
    useInboxStore.setState({ prefs: await InboxApi.updatePreferences({ mute: { scheduleId, muted } }) });
    return true;
  } catch {
    mark(!muted);
    return false;
  }
}

export async function setRunNotices(change: { email?: boolean; slack?: boolean }): Promise<boolean> {
  try {
    useInboxStore.setState({ prefs: await InboxApi.updatePreferences({ runNotices: change }) });
    return true;
  } catch {
    return false;
  }
}

/** Takes an ask off the list once it is settled in place (a skill approved or rejected): it is gone at its source. */
export function dropItem(id: string): void {
  useInboxStore.setState((s) => ({ items: s.items.filter((i) => i.id !== id) }));
  void refreshCount();
}
