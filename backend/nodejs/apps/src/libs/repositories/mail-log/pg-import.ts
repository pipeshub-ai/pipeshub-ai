/** Documents of the `mailInfo` collection as rows of `platform.mail_log` (partitioned by month; older months land in the default partition). */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

const strs = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

export async function importMailDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    if (
      !id ||
      !org ||
      typeof d.from !== 'string' ||
      typeof d.emailTemplateType !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      // the log table is per organisation; an entry that names none cannot be stored
      stats.skipped.push({
        id: String(d._id),
        reason:
          'mail log entry without a usable orgId (or from, emailTemplateType, createdAt)',
      });
      continue;
    }
    rows.push({
      org_id: org,
      id,
      created_at: d.createdAt,
      subject: typeof d.subject === 'string' ? d.subject : null,
      from_address: d.from,
      to_addresses: strs(d.to),
      cc_addresses: strs(d.cc),
      email_template_type: d.emailTemplateType,
      version: typeof d.__v === 'number' ? d.__v : 0,
      updated_at: d.updatedAt ?? d.createdAt,
    });
  }
  await writePlan(k, [{ table: 'platform.mail_log', rows }], stats);
  return stats;
}
