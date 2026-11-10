/**
 * Documents of the `notifications` collection as rows of `platform.notifications`. The table is partitioned by month of
 * `created_at`; a month with no partition lands in the default partition, so the copy needs no DDL (run `db.maintain monthly`
 * for the months in the data beforehand to get them in their own partitions).
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export async function importNotificationDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const to = idText(d.assignedTo);
    if (
      !id ||
      !org ||
      !to ||
      typeof d.type !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'notification without a usable _id, orgId, assignedTo, type or createdAt',
      });
      continue;
    }
    rows.push({
      org_id: org,
      id,
      created_at: d.createdAt,
      type: d.type,
      title: str(d.title),
      message: str(d.message),
      redirect_link: str(d.redirectLink),
      severity: str(d.severity),
      status: d.status ?? 'unread',
      origin_service: str(d.originService),
      assigned_to: to,
      payload: d.payload ?? null,
      is_deleted: d.isDeleted ?? false,
      deleted_by: idText(d.deletedBy),
      version: typeof d.__v === 'number' ? d.__v : 0,
      updated_at: d.updatedAt ?? d.createdAt,
    });
  }
  await writePlan(k, [{ table: 'platform.notifications', rows }], stats);
  return stats;
}
