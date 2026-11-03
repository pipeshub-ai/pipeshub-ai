/** Mongo documents of the `search` collection (`searches`) as rows of `conversation.semantic_searches` and its members. */
import type { Tx as KernelTx } from '../../db';
import { memberRows, nullsOf } from '../conversation/pg-import';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export async function importSearchDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'searches')
    throw new Error(
      `the saved-search repository does not import '${collection}'`,
    );
  const stats = emptyStats(docs.length);
  const searches: Row[] = [];
  const members: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const user = idText(d.userId);
    if (
      !id ||
      !org ||
      !user ||
      typeof d.query !== 'string' ||
      typeof d.limit !== 'number'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'searches document without a usable _id, orgId, userId, query or limit',
      });
      continue;
    }
    searches.push({
      org_id: org,
      id,
      query: d.query,
      result_limit: d.limit,
      user_id: user,
      citation_ids: (Array.isArray(d.citationIds) ? d.citationIds : [])
        .map(idText)
        .filter((x) => x !== null),
      records: d.records && typeof d.records === 'object' ? d.records : {},
      is_shared: d.isShared ?? false,
      share_link: str(d.shareLink),
      is_archived: d.isArchived ?? false,
      archived_by: idText(d.archivedBy),
      version: typeof d.__v === 'number' ? d.__v : 0,
      nulls: nullsOf(d),
      created_at: d.createdAt,
      updated_at: d.updatedAt ?? d.createdAt,
    });
    for (const m of memberRows(id, d.sharedWith))
      members.push({ org_id: org, search_id: id, ...m });
  }
  await writePlan(
    k,
    [
      { table: 'conversation.semantic_searches', rows: searches },
      { table: 'conversation.semantic_search_members', rows: members },
    ],
    stats,
  );
  return stats;
}
