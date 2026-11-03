/** Mongo `orgAuthConfig` documents as rows of `identity.org_auth_config` (one per organisation: the first copied stays). */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { bool, needId, stamps, str, version } from '../identity/import-util';

export async function importAuthConfigDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'orgAuthConfig')
    throw new Error(
      `the auth-config repository does not import '${collection}'`,
    );
  const stats = emptyStats(docs.length);
  const rows = docs.map((d) => {
    const id = needId(d, 'orgAuthConfig');
    const org = idText(d.orgId);
    if (!org) throw new Error(`orgAuthConfig ${id} has no usable orgId`);
    return {
      org_id: org,
      id,
      domain: str(d.domain),
      auth_steps: d.authSteps ?? [],
      removed_sign_in_methods: Array.isArray(d.removedSignInMethods)
        ? d.removedSignInMethods.map(String)
        : [],
      is_deleted: bool(d.isDeleted),
      version: version(d),
      ...stamps(d, id),
    };
  });
  await writePlan(k, [{ table: 'identity.org_auth_config', rows }], stats);
  return stats;
}
