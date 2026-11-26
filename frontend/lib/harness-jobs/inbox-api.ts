import { apiClient } from '@/lib/api';
import type { InboxCounts } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-counts';
import type { InboxFeed } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-feed';
import type { InboxNoticesResult } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-notices-result';
import type { InboxPreferences } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-preferences';
import type { InboxFilter, InboxView, PauseChoice } from './inbox';

/**
 * The Inbox's reads and writes (UI-B2, ADR-INBOX-01). The person is always the token's on the server: an id of
 * anyone else's matches nothing. Clear all deletes archived notices in one call (`POST …/notices/delete`, ADR-INBOX-01
 * A2), the one Inbox action that cannot be undone.
 */

const BASE = '/api/v1/agent-harness/inbox';

export type NoticeAction = 'read' | 'unread' | 'archive' | 'unarchive' | 'restore';
export type NoticeSelection = { ids: string[] } | { all: true } | { conversationId: string };

/** Notices one action names at most (the route's cap). */
export const MAX_NOTICE_IDS = 100;
/** Archived notices one `{all: true}` delete takes at most (the route's cap). */
const MAX_DELETE_ALL = 500;

export const InboxApi = {
  async feed(options: { filter?: InboxFilter; view?: InboxView; cursor?: string; limit?: number } = {}): Promise<InboxFeed> {
    const { data } = await apiClient.get<InboxFeed>(BASE, {
      params: {
        ...(options.filter && options.filter !== 'all' ? { filter: options.filter } : {}),
        ...(options.view && options.view !== 'inbox' ? { view: options.view } : {}),
        ...(options.cursor ? { cursor: options.cursor } : {}),
        ...(options.limit ? { limit: options.limit } : {}),
      },
      suppressErrorToast: true,
    });
    return data;
  },

  async count(): Promise<InboxCounts> {
    const { data } = await apiClient.get<InboxCounts>(`${BASE}/count`, { suppressErrorToast: true });
    return data;
  },

  /** One action on notices; ids past the route's cap go in more than one call, and their changes are joined. */
  async notices(action: NoticeAction, selection: NoticeSelection): Promise<InboxNoticesResult> {
    if ('ids' in selection && selection.ids.length > MAX_NOTICE_IDS) {
      let last: InboxNoticesResult | null = null;
      const changed: string[] = [];
      for (let i = 0; i < selection.ids.length; i += MAX_NOTICE_IDS) {
        last = await InboxApi.notices(action, { ids: selection.ids.slice(i, i + MAX_NOTICE_IDS) });
        changed.push(...last.changed);
      }
      return { changed, counts: (last as InboxNoticesResult).counts };
    }
    const { data } = await apiClient.patch<InboxNoticesResult>(`${BASE}/notices`, { action, ...selection }, { suppressErrorToast: true });
    return data;
  },

  async preferences(): Promise<InboxPreferences> {
    const { data } = await apiClient.get<InboxPreferences>(`${BASE}/preferences`, { suppressErrorToast: true });
    return data;
  },

  async updatePreferences(
    change: { mute: { scheduleId: string; muted: boolean } } | { pause: { choice: PauseChoice; timeZone?: string } } | { runNotices: { email?: boolean; slack?: boolean } },
  ): Promise<InboxPreferences> {
    const { data } = await apiClient.patch<InboxPreferences>(`${BASE}/preferences`, change, { suppressErrorToast: true });
    return data;
  },

  /**
   * Deletes archived notices for good (`POST …/inbox/notices/delete`): only Clear all, which confirms first. The server
   * checks every id against the person's own archive. `all` takes up to 500 a call, so a full page asks once more.
   */
  async deleteNotices(selection: { ids: string[] } | { all: true }): Promise<InboxNoticesResult> {
    const changed: string[] = [];
    let last: InboxNoticesResult | null = null;
    for (let round = 0; round < 20; round += 1) {
      const { data } = await apiClient.post<InboxNoticesResult>(`${BASE}/notices/delete`, selection, { suppressErrorToast: true });
      last = data;
      changed.push(...data.changed);
      if (!('all' in selection) || data.changed.length < MAX_DELETE_ALL) break;
    }
    return { changed, counts: (last as InboxNoticesResult).counts };
  },
};

/** The person's own clock, so "until tomorrow" means 09:00 where they are. */
export function personTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}
