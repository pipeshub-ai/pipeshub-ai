/** Mongo `userActivities` documents as rows of `identity.user_activities`. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { bool, lowerEmail, needId, stamps, str } from '../identity/import-util';

export async function importActivityDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'userActivities')
    throw new Error(`the activity repository does not import '${collection}'`);
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = needId(d, 'userActivities');
    const org = idText(d.orgId);
    // an attempt from before any organisation was known has no row to hang on: the application attributes a new one to the first organisation
    if (!org) {
      stats.skipped.push({ id, reason: 'activity without an organisation' });
      continue;
    }
    rows.push({
      org_id: org,
      id,
      user_id: idText(d.userId),
      email: lowerEmail(d.email),
      activity_type: d.activityType,
      login_mode: str(d.loginMode),
      ip_address: d.ipAddress,
      is_deleted: bool(d.isDeleted),
      ...stamps(d, id),
    });
  }
  await writePlan(k, [{ table: 'identity.user_activities', rows }], stats);
  return stats;
}
