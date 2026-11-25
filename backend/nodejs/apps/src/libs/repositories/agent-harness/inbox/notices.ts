import {
  filterLists,
  noticeItem,
  noticeKind,
  type InboxItem,
} from '../../../../modules/agent-harness/domain/inbox';
import type {
  InboxNotices,
  NoticeAction,
  NoticePage,
  NoticeSelection,
} from '../../../../modules/agent-harness/ports/inbox';
import { getNotificationRepository } from '../../notification';
import type {
  NotificationRepository,
  NotificationRow,
  NotificationScope,
} from '../../notification/types';

/** Rows one read of the repository returns. */
const SCAN_PAGE = 100;
/** Reads a filtered page may make before it hands back a cursor to continue from. */
const SCAN_READS = 5;
/** Notices an "all" action changes at most in one call. */
const MAX_BULK = 500;

const FINISHED_KINDS = new Set(['run_finished', 'run_not_finished']);

/**
 * The person's notices over main's notification repository (`getNotificationRepository`: MongoDB or PostgreSQL,
 * whichever is the document store). Filters main's list does not know (a kind) are applied while reading forward,
 * a bounded number of reads per page, so a sparse filter still answers quickly and carries on from its cursor.
 */
export class RepositoryInboxNotices implements InboxNotices {
  constructor(
    private readonly repository: () => NotificationRepository = getNotificationRepository,
  ) {}

  async page(
    viewer: NotificationScope,
    query: Parameters<InboxNotices['page']>[1],
  ): Promise<NoticePage> {
    const status =
      query.view === 'archived'
        ? 'archived'
        : query.filter === 'unread'
          ? 'unread'
          : null;
    const items: InboxItem[] = [];
    let cursor =
      query.cursor !== undefined
        ? { createdAt: new Date(query.cursor.at), id: query.cursor.id }
        : undefined;
    for (let read = 0; read < SCAN_READS; read += 1) {
      const rows = await this.repository().list(viewer, {
        status,
        ...(cursor !== undefined ? { cursor } : {}),
        limit: SCAN_PAGE,
      });
      const scanned = rows.slice(0, SCAN_PAGE);
      for (const row of scanned) {
        const item = noticeItem(row, query.muted);
        if (query.view === 'archived' || filterLists(query.filter, item))
          items.push(item);
        if (items.length === query.limit) {
          const more =
            row !== scanned[scanned.length - 1] || rows.length > SCAN_PAGE;
          return more
            ? { items, next: { at: row.createdAt.getTime(), id: row._id } }
            : { items };
        }
      }
      if (rows.length <= SCAN_PAGE) return { items };
      const last = scanned.at(-1);
      if (last === undefined) break;
      cursor = { createdAt: last.createdAt, id: last._id };
    }
    return {
      items,
      ...(cursor !== undefined
        ? { next: { at: cursor.createdAt.getTime(), id: cursor.id } }
        : {}),
    };
  }

  async unreadCount(viewer: NotificationScope): Promise<number> {
    return (await this.repository().counts(viewer)).unread;
  }

  async apply(
    viewer: NotificationScope,
    action: NoticeAction,
    selection: NoticeSelection,
  ): Promise<string[]> {
    const repo = this.repository();
    const targets =
      'ids' in selection
        ? [...new Set(selection.ids)]
        : await this.selected(viewer, action, selection);
    if (action === 'read' && 'all' in selection) {
      await repo.markAllRead(viewer);
      return targets;
    }
    const changed: string[] = [];
    for (const id of targets) {
      let row: NotificationRow | null;
      try {
        row =
          action === 'unarchive' || action === 'restore'
            ? await repo.unarchive(viewer, id)
            : await repo.setStatus(
                viewer,
                id,
                action === 'archive' ? 'archived' : action,
              );
      } catch {
        // An id in another store's shape (or none at all) names nothing of the caller's.
        row = null;
      }
      if (row !== null) changed.push(row._id);
    }
    return changed;
  }

  async removeArchived(
    viewer: NotificationScope,
    selection: { ids: readonly string[] } | { all: true },
  ): Promise<string[]> {
    const wanted = 'ids' in selection ? new Set(selection.ids) : null;
    if (wanted !== null && wanted.size === 0) return [];
    // Only an archived notice is deleted: the ids are checked against the person's own archive, never trusted.
    const archived = await this.archivedIds(viewer, wanted);
    const repo = this.repository();
    const deleted: string[] = [];
    for (const id of archived) {
      let row: NotificationRow | null;
      try {
        row = await repo.softDelete(viewer, id);
      } catch {
        row = null;
      }
      if (row !== null) deleted.push(row._id);
    }
    return deleted;
  }

  /** The person's archived notice ids, newest first: those of `wanted` (all found, or the archive read), else up to the bulk bound. */
  private async archivedIds(
    viewer: NotificationScope,
    wanted: ReadonlySet<string> | null,
  ): Promise<string[]> {
    const ids: string[] = [];
    let cursor: { createdAt: Date; id: string } | undefined;
    for (;;) {
      const rows = await this.repository().list(viewer, {
        status: 'archived',
        ...(cursor !== undefined ? { cursor } : {}),
        limit: SCAN_PAGE,
      });
      const scanned = rows.slice(0, SCAN_PAGE);
      for (const row of scanned) {
        if (wanted === null || wanted.has(row._id)) ids.push(row._id);
        if (ids.length === (wanted?.size ?? MAX_BULK)) return ids;
      }
      if (rows.length <= SCAN_PAGE) return ids;
      const last = scanned.at(-1);
      if (last === undefined) return ids;
      cursor = { createdAt: last.createdAt, id: last._id };
    }
  }

  /** The ids an "all" or a conversation's selection names now. */
  private async selected(
    viewer: NotificationScope,
    action: NoticeAction,
    selection: Exclude<NoticeSelection, { ids: readonly string[] }>,
  ): Promise<string[]> {
    const status =
      'conversationId' in selection || action === 'read'
        ? 'unread'
        : action === 'archive'
          ? 'read'
          : action === 'unread'
            ? 'read'
            : 'archived';
    const ids: string[] = [];
    let cursor: { createdAt: Date; id: string } | undefined;
    while (ids.length < MAX_BULK) {
      const rows = await this.repository().list(viewer, {
        status,
        ...(cursor !== undefined ? { cursor } : {}),
        limit: SCAN_PAGE,
      });
      const scanned = rows.slice(0, SCAN_PAGE);
      for (const row of scanned) {
        if ('conversationId' in selection) {
          if (
            row.payload?.['conversationId'] === selection.conversationId &&
            FINISHED_KINDS.has(noticeKind(row))
          ) {
            ids.push(row._id);
          }
        } else {
          ids.push(row._id);
        }
        if (ids.length === MAX_BULK) break;
      }
      if (rows.length <= SCAN_PAGE) break;
      const last = scanned.at(-1);
      if (last === undefined) break;
      cursor = { createdAt: last.createdAt, id: last._id };
    }
    return ids;
  }
}
