/** Mongo `user-dps` documents as rows of `identity.user_pictures` (one per person: the first copied stays). */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { needId, str, version } from '../identity/import-util';

export async function importPictureDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'user-dps')
    throw new Error(`the picture repository does not import '${collection}'`);
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = needId(d, 'user-dps');
    const org = idText(d.orgId);
    const user = idText(d.userId);
    if (!org || !user) {
      stats.skipped.push({
        id,
        reason: 'picture without a usable orgId or userId',
      });
      continue;
    }
    rows.push({
      org_id: org,
      user_id: user,
      id,
      pic: str(d.pic),
      pic_url: str(d.picUrl),
      mime_type: str(d.mimeType),
      version: version(d),
    });
  }
  await writePlan(k, [{ table: 'identity.user_pictures', rows }], stats);
  return stats;
}
