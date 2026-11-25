import {
  encodeNoticeCursor,
  filterListsNotices,
  filterListsWaiting,
  filterLists,
  inboxCounts,
  inboxOrder,
  type InboxCounts,
  type InboxFilter,
  type InboxItem,
  type InboxPreferences,
  type InboxView,
  type NoticeCursor,
  type PauseChoice,
  pauseFor,
} from '../domain/inbox';
import type {
  InboxCountPublisher,
  InboxNotices,
  InboxPreferenceStore,
  InboxViewer,
  InboxWaitingSource,
  NoticeAction,
  NoticePage,
  NoticeSelection,
} from '../ports/inbox';

export const INBOX_DEFAULT_LIMIT = 30;
export const INBOX_MAX_LIMIT = 50;
/** Asks one source lists at most; an Inbox with more shows the newest. */
export const WAITING_PER_SOURCE = 50;

export interface InboxFeedQuery {
  view: InboxView;
  filter: InboxFilter;
  cursor?: NoticeCursor;
  limit: number;
}

export interface InboxFeed {
  items: InboxItem[];
  /** The next page of notices; absent on the last page. Asks are all on the first page. */
  cursor?: string;
  counts: InboxCounts;
  /** Sources that could not answer this time; their items are missing, never guessed. */
  partial?: Array<InboxWaitingSource['name']>;
}

export interface PreferencesPatch {
  mute?: { scheduleId: string; muted: boolean };
  pause?: { choice: PauseChoice; timeZone?: string };
  runNotices?: Partial<InboxPreferences['runNotices']>;
}

/** The schedule a mute names, as the caller may see it; null when it is not theirs to receive notices of. */
export type MutableSchedule = (
  orgId: string,
  userId: string,
  isAdmin: boolean,
  scheduleId: string,
) => Promise<boolean>;

export class InboxScheduleNotFoundError extends Error {
  constructor() {
    super('Schedule not found');
    this.name = 'InboxScheduleNotFoundError';
  }
}

/**
 * One Inbox per person (G2, ADR-INBOX-01): the asks waiting on them merged with their notices, the counts the badge
 * shows, the actions on notices, and their settings. Asks come from their own services on every read (they are
 * decided elsewhere, so no copy goes stale); notices are main's stored notifications.
 */
export class InboxFeedService {
  constructor(
    private readonly deps: {
      notices: InboxNotices;
      waiting: readonly InboxWaitingSource[];
      preferences: InboxPreferenceStore;
      schedules: MutableSchedule;
      publisher?: InboxCountPublisher;
      /**
       * How many of the person's jobs are at work (`isWorkingJob`, CJ14-FU-1), for the sidebar's Jobs entry, with
       * the counts the page already reads; absent, or a read that fails: the counts carry no `working`.
       */
      workingJobs?: (orgId: string, userId: string) => Promise<number>;
      /** Whether the person is an admin of the org, read on the server; absent: nobody is. */
      isOrgAdmin?: (orgId: string, userId: string) => Promise<boolean>;
      now?: () => number;
    },
  ) {}

  private now(): number {
    return this.deps.now?.() ?? Date.now();
  }

  async feed(viewer: InboxViewer, query: InboxFeedQuery): Promise<InboxFeed> {
    const prefs = await this.deps.preferences.preferences(
      viewer.orgId,
      viewer.userId,
    );
    const muted = new Set(prefs.mutedSchedules);
    const listsWaiting =
      query.view === 'inbox' &&
      query.cursor === undefined &&
      filterListsWaiting(query.filter);
    const [asks, page, unread, working] = await Promise.all([
      this.asks(viewer, listsWaiting ? 'feed' : 'count'),
      filterListsNotices(query.filter)
        ? this.deps.notices.page(viewer, {
            view: query.view,
            filter: query.filter,
            ...(query.cursor !== undefined ? { cursor: query.cursor } : {}),
            limit: query.limit,
            muted,
          })
        : Promise.resolve<NoticePage>({ items: [] }),
      this.deps.notices.unreadCount(viewer),
      this.working(viewer),
    ]);
    const waiting = listsWaiting
      ? asks.items.filter((item) => filterLists(query.filter, item))
      : [];
    const items = [...waiting, ...page.items].sort(inboxOrder);
    return {
      items,
      ...(page.next !== undefined
        ? { cursor: encodeNoticeCursor(page.next) }
        : {}),
      counts: withWorking(
        inboxCounts(unread, asks.items.length, prefs, this.now()),
        working,
      ),
      ...(asks.partial.length > 0 ? { partial: asks.partial } : {}),
    };
  }

  async counts(viewer: InboxViewer): Promise<InboxCounts> {
    const [prefs, asks, unread, working] = await Promise.all([
      this.deps.preferences.preferences(viewer.orgId, viewer.userId),
      this.asks(viewer, 'count'),
      this.deps.notices.unreadCount(viewer),
      this.working(viewer),
    ]);
    return withWorking(
      inboxCounts(unread, asks.items.length, prefs, this.now()),
      working,
    );
  }

  /** The person's working jobs; null when this server cannot count them now (the client then shows none). */
  private async working(viewer: InboxViewer): Promise<number | null> {
    if (this.deps.workingJobs === undefined) return null;
    try {
      return await this.deps.workingJobs(viewer.orgId, viewer.userId);
    } catch {
      return null;
    }
  }

  /** Changes the caller's notices and tells their open sessions the new counts. */
  async apply(
    viewer: InboxViewer,
    action: NoticeAction,
    selection: NoticeSelection,
  ): Promise<{ changed: string[]; counts: InboxCounts }> {
    const changed = (
      await this.deps.notices.apply(viewer, action, selection)
    ).map((id) => `n:${id}`);
    const counts = await this.counts(viewer);
    if (changed.length > 0) this.deps.publisher?.publish(viewer.userId, counts);
    return { changed, counts };
  }

  /** Deletes the caller's archived notices (one, several, or all) and tells their open sessions the new counts. */
  async removeArchived(
    viewer: InboxViewer,
    selection: { ids: readonly string[] } | { all: true },
  ): Promise<{ changed: string[]; counts: InboxCounts }> {
    const changed = (
      await this.deps.notices.removeArchived(viewer, selection)
    ).map((id) => `n:${id}`);
    const counts = await this.counts(viewer);
    if (changed.length > 0) this.deps.publisher?.publish(viewer.userId, counts);
    return { changed, counts };
  }

  preferences(viewer: InboxViewer): Promise<InboxPreferences> {
    return this.deps.preferences.preferences(viewer.orgId, viewer.userId);
  }

  async updatePreferences(
    viewer: InboxViewer,
    patch: PreferencesPatch,
  ): Promise<InboxPreferences> {
    const now = this.now();
    const store = this.deps.preferences;
    let prefs: InboxPreferences | null = null;
    if (patch.mute !== undefined) {
      if (
        patch.mute.muted &&
        !(await this.deps.schedules(
          viewer.orgId,
          viewer.userId,
          viewer.isAdmin,
          patch.mute.scheduleId,
        ))
      ) {
        throw new InboxScheduleNotFoundError();
      }
      prefs = await store.setMuted(
        viewer.orgId,
        viewer.userId,
        patch.mute.scheduleId,
        patch.mute.muted,
        now,
      );
    }
    if (patch.pause !== undefined) {
      prefs = await store.setPause(
        viewer.orgId,
        viewer.userId,
        pauseFor(patch.pause.choice, now, patch.pause.timeZone),
        now,
      );
    }
    if (patch.runNotices !== undefined) {
      prefs = await store.setRunNotices(
        viewer.orgId,
        viewer.userId,
        patch.runNotices,
        now,
      );
    }
    const result =
      prefs ?? (await store.preferences(viewer.orgId, viewer.userId));
    if (patch.pause !== undefined)
      this.deps.publisher?.publish(viewer.userId, await this.counts(viewer));
    return result;
  }

  /** The announcements counting now, by `orgId:userId`: true when another was asked for while it counted. */
  private readonly announcing = new Map<string, boolean>();

  /**
   * Tells the person's sessions their counts now, after a change no request of theirs made (a run raised or settled
   * an ask, a run-end notice; ADR-INBOX-02). Their role is looked up here, never taken from the caller, so an admin's
   * push counts the asks only admins see. A failure to count (or to look up the role) is not the caller's to see.
   * Announcements for one person do not overlap: one asked for while another counts makes one more count after it,
   * so a burst of commits costs two counts and the last push is never older than the last change.
   */
  async announce(orgId: string, userId: string): Promise<void> {
    if (this.deps.publisher === undefined) return;
    const key = `${orgId}:${userId}`;
    if (this.announcing.has(key)) {
      this.announcing.set(key, true);
      return;
    }
    this.announcing.set(key, false);
    try {
      do {
        this.announcing.set(key, false);
        await this.announceOnce(orgId, userId);
      } while (this.announcing.get(key) === true);
    } finally {
      this.announcing.delete(key);
    }
  }

  private async announceOnce(orgId: string, userId: string): Promise<void> {
    try {
      const isAdmin = (await this.deps.isOrgAdmin?.(orgId, userId)) ?? false;
      const counts = await this.counts({ orgId, userId, isAdmin });
      // No credential here: what only the person's own can count is left out, and the client recounts (FIXMS1-FU-1).
      const recount = this.deps.waiting.some(
        (source) => source.credentialOnly === true,
      );
      this.deps.publisher?.publish(
        userId,
        recount ? { ...counts, recount: true } : counts,
      );
    } catch {
      // The next read recounts; a push is a hint, never the record.
    }
  }

  private async asks(
    viewer: InboxViewer,
    purpose: 'feed' | 'count',
  ): Promise<{
    items: InboxItem[];
    partial: Array<InboxWaitingSource['name']>;
  }> {
    const answers = await Promise.allSettled(
      this.deps.waiting.map((source) =>
        source.waiting(viewer, WAITING_PER_SOURCE, purpose),
      ),
    );
    const items: InboxItem[] = [];
    const partial: Array<InboxWaitingSource['name']> = [];
    this.deps.waiting.forEach((source, index) => {
      const answer = answers[index];
      if (answer?.status === 'fulfilled') items.push(...answer.value);
      else partial.push(source.name);
    });
    const seen = new Set<string>();
    return {
      items: items
        .filter((item) =>
          seen.has(item.id) ? false : (seen.add(item.id), true),
        )
        .sort(inboxOrder),
      partial,
    };
  }
}

const withWorking = (
  counts: InboxCounts,
  working: number | null,
): InboxCounts => (working === null ? counts : { ...counts, working });
