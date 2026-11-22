/** Mongo `usedPasswordResetLinks` documents as rows of `identity.used_reset_links`. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { needId, stamps } from '../identity/import-util';

export async function importResetLinkDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'usedPasswordResetLinks')
    throw new Error(
      `the reset-link repository does not import '${collection}'`,
    );
  const stats = emptyStats(docs.length);
  const rows = docs.flatMap((d) => {
    const id = needId(d, 'usedPasswordResetLinks');
    const org = idText(d.orgId);
    if (!org) {
      stats.skipped.push({ id, reason: 'reset link without a usable orgId' });
      return [];
    }
    return [
      {
        org_id: org,
        link_hash: d.linkHash,
        user_id: d.userId,
        expires_at: d.expiresAt,
        ...stamps(d, id),
      },
    ];
  });
  await writePlan(k, [{ table: 'identity.used_reset_links', rows }], stats);
  return stats;
}
