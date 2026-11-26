/**
 * Main's notification repository in memory, as its Mongo and PostgreSQL implementations behave: newest first by
 * (createdAt, id), `limit + 1` rows, archived only when asked for, deleted never; every call scoped to its person.
 */
import type {
  NewNotification,
  NotificationRepository,
  NotificationRow,
  NotificationScope,
} from '../../../../src/libs/repositories/notification/types';

export class FakeNotifications implements NotificationRepository {
  readonly rows: NotificationRow[] = [];
  private next = 1;

  seed(scope: NotificationScope, over: Partial<NotificationRow> & { createdAt: Date }): NotificationRow {
    const row: NotificationRow = {
      _id: `n${String(this.next++).padStart(6, '0')}`,
      orgId: scope.orgId,
      assignedTo: scope.userId,
      type: 'X',
      status: 'unread',
      isDeleted: false,
      ...over,
    };
    this.rows.push(row);
    return row;
  }

  async insert(row: NewNotification): Promise<NotificationRow> {
    return this.seed({ orgId: row.orgId, userId: row.assignedTo }, { ...(row as Partial<NotificationRow>), createdAt: new Date() });
  }

  async insertMany(rows: NewNotification[]): Promise<NotificationRow[]> {
    return Promise.all(rows.map((row) => this.insert(row)));
  }

  async exists(scope: NotificationScope, id: string): Promise<boolean> {
    return this.rows.some((r) => r.orgId === scope.orgId && r.assignedTo === scope.userId && r._id === id);
  }

  private mine(scope: NotificationScope): NotificationRow[] {
    return this.rows
      .filter((r) => r.orgId === scope.orgId && r.assignedTo === scope.userId && !r.isDeleted)
      .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime() || (a._id < b._id ? 1 : -1));
  }

  async list(
    scope: NotificationScope,
    query: { status: string | null; cursor?: { createdAt: Date; id: string }; limit: number },
  ): Promise<NotificationRow[]> {
    return this.mine(scope)
      .filter((r) => (query.status === null ? r.status !== 'archived' : r.status === query.status))
      .filter(
        (r) =>
          query.cursor === undefined ||
          r.createdAt.getTime() < query.cursor.createdAt.getTime() ||
          (r.createdAt.getTime() === query.cursor.createdAt.getTime() && r._id < query.cursor.id),
      )
      .slice(0, query.limit + 1)
      .map((r) => ({ ...r }));
  }

  async counts(scope: NotificationScope): Promise<{ unread: number; read: number; archived: number }> {
    const mine = this.mine(scope);
    return {
      unread: mine.filter((r) => r.status === 'unread').length,
      read: mine.filter((r) => r.status === 'read').length,
      archived: mine.filter((r) => r.status === 'archived').length,
    };
  }

  async markAllRead(scope: NotificationScope): Promise<number> {
    const unread = this.mine(scope).filter((r) => r.status === 'unread');
    for (const r of unread) r.status = 'read';
    return unread.length;
  }

  async setStatus(scope: NotificationScope, id: string, status: 'read' | 'unread' | 'archived'): Promise<NotificationRow | null> {
    const row = this.mine(scope).find((r) => r._id === id && r.status !== 'archived');
    if (row === undefined) return null;
    row.status = status;
    return { ...row };
  }

  async unarchive(scope: NotificationScope, id: string): Promise<NotificationRow | null> {
    const row = this.mine(scope).find((r) => r._id === id && r.status === 'archived');
    if (row === undefined) return null;
    row.status = 'read';
    return { ...row };
  }

  async softDelete(scope: NotificationScope, id: string): Promise<NotificationRow | null> {
    const row = this.mine(scope).find((r) => r._id === id);
    if (row === undefined) return null;
    row.isDeleted = true;
    return { ...row };
  }

  async eraseForConversation(): Promise<number> {
    return 0;
  }

  async eraseForOrg(): Promise<number> {
    return 0;
  }
}
