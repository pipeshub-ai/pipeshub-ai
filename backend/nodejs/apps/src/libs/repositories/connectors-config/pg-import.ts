/** Documents of the `connectorsConfig` collection as rows of `platform.connectors_config` (one per connector name in an org; the first wins). */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

export async function importConnectorsConfigDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const by = idText(d.lastUpdatedBy);
    if (
      !id ||
      !org ||
      !by ||
      typeof d.name !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'connector config without a usable _id, orgId, lastUpdatedBy, name or createdAt',
      });
      continue;
    }
    rows.push({
      org_id: org,
      name: d.name,
      id,
      is_enabled: d.isEnabled ?? true,
      last_updated_by: by,
      version: typeof d.__v === 'number' ? d.__v : 0,
      created_at: d.createdAt,
      updated_at: d.updatedAt ?? d.createdAt,
    });
  }
  await writePlan(k, [{ table: 'platform.connectors_config', rows }], stats);
  return stats;
}
