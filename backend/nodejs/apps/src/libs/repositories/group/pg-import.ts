/** Mongo `userGroups` documents as rows of `identity.user_groups` and `identity.user_group_members`. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import { bool, needId, stamps, str, version } from '../identity/import-util';

export function groupRows(d: NeutralDoc): { group: Row; members: Row[] } {
  const id = needId(d, 'group');
  const org = idText(d.orgId);
  if (!org) throw new Error(`group ${id} has no usable orgId`);
  if (typeof d.slug !== 'string') throw new Error(`group ${id} has no slug`);
  const seen = new Set<string>();
  const members: Row[] = [];
  for (const u of Array.isArray(d.users) ? d.users : []) {
    const uid = idText(u);
    // a person listed twice keeps the first place (the table holds a person once per group)
    if (!uid || seen.has(uid)) continue;
    seen.add(uid);
    members.push({
      org_id: org,
      group_id: id,
      user_id: uid,
      pos: members.length,
    });
  }
  return {
    group: {
      org_id: org,
      id,
      slug: d.slug,
      name: d.name,
      type: d.type,
      is_deleted: bool(d.isDeleted),
      deleted_by: idText(d.deletedBy) ?? str(d.deletedBy),
      version: version(d),
      ...stamps(d, id),
    },
    members,
  };
}

export async function importGroupDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'userGroups')
    throw new Error(`the group repository does not import '${collection}'`);
  const stats = emptyStats(docs.length);
  const parts = docs.map(groupRows);
  await writePlan(
    k,
    [
      { table: 'identity.user_groups', rows: parts.map((p) => p.group) },
      {
        table: 'identity.user_group_members',
        rows: parts.flatMap((p) => p.members),
      },
    ],
    stats,
  );
  return stats;
}
