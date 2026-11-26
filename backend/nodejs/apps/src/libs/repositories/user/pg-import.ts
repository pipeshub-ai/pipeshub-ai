/** Mongo `users` documents as rows of `identity.users`. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import {
  bool,
  lowerEmail,
  needId,
  stamps,
  str,
  version,
} from '../identity/import-util';

export function userRow(d: NeutralDoc): Row {
  const id = needId(d, 'user');
  const org = idText(d.orgId);
  if (!org) throw new Error(`user ${id} has no usable orgId`);
  if (typeof d.slug !== 'string') throw new Error(`user ${id} has no slug`);
  return {
    org_id: org,
    id,
    slug: d.slug,
    full_name: str(d.fullName),
    first_name: str(d.firstName),
    last_name: str(d.lastName),
    middle_name: str(d.middleName),
    email: lowerEmail(d.email),
    mobile: str(d.mobile),
    has_logged_in: bool(d.hasLoggedIn),
    designation: str(d.designation),
    kind: str(d.kind) ?? 'human',
    description: str(d.description),
    is_disabled: bool(d.isDisabled),
    restore_op_id: str(d.restoreOpId),
    role: str(d.role) ?? 'member',
    address: d.address ?? null,
    is_deleted: bool(d.isDeleted),
    deleted_by: str(d.deletedBy),
    version: version(d),
    ...stamps(d, id),
  };
}

export async function importUserDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'users')
    throw new Error(`the user repository does not import '${collection}'`);
  const stats = emptyStats(docs.length);
  await writePlan(
    k,
    [{ table: 'identity.users', rows: docs.map(userRow) }],
    stats,
  );
  return stats;
}
