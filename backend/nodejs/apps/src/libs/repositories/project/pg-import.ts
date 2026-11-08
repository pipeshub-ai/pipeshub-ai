/** Documents of the `projects` collection as rows of `content.projects` and `content.project_members` (one row per `members[]` entry, `pos` = array index). */
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
const EMPTY_LISTS = { apps: [], kb: [], recordGroups: [], records: [] };
const obj = (v: unknown, dflt: unknown): unknown =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? v : dflt;

export function importProjectDocs(docs: NeutralDoc[]): {
  projects: Row[];
  members: Row[];
  skipped: ImportStats['skipped'];
} {
  const projects: Row[] = [];
  const members: Row[] = [];
  const skipped: ImportStats['skipped'] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const owner = idText(d.userId);
    if (
      !id ||
      !org ||
      !owner ||
      typeof d.name !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      skipped.push({
        id: String(d._id),
        reason:
          'project without a usable _id, orgId, userId, name or createdAt',
      });
      continue;
    }
    projects.push({
      org_id: org,
      id,
      user_id: owner,
      name: d.name,
      description: str(d.description),
      icon: str(d.icon),
      color: str(d.color),
      instructions: str(d.instructions),
      knowledge_scope: obj(d.knowledgeScope, EMPTY_LISTS),
      applied_filters: obj(d.appliedFilters, EMPTY_LISTS),
      tools: Array.isArray(d.tools) ? d.tools : [],
      linked_knowledge_base_id: str(d.linkedKnowledgeBaseId),
      visibility: d.visibility ?? 'private',
      chat_sharing: d.chatSharing ?? 'private',
      is_pinned: d.isPinned ?? false,
      pinned_by: (Array.isArray(d.pinnedBy) ? d.pinnedBy : []).flatMap(
        (u) => idText(u) ?? [],
      ),
      is_archived: d.isArchived ?? false,
      archived_by: idText(d.archivedBy),
      is_deleted: d.isDeleted ?? false,
      deleted_by: idText(d.deletedBy),
      last_activity_at:
        typeof d.lastActivityAt === 'number'
          ? new Date(d.lastActivityAt).toISOString()
          : d.createdAt,
      metadata: d.metadata ?? null,
      version: typeof d.__v === 'number' ? d.__v : 0,
      created_at: d.createdAt,
      updated_at: d.updatedAt ?? d.createdAt,
    });
    const seen = new Set<string>();
    (Array.isArray(d.members) ? d.members : []).forEach(
      (m: Record<string, unknown>, pos: number) => {
        const type = (m.principalType as string | undefined) ?? 'user';
        // a team is named by its graph id (a UUID or all_<org>), a user by an ObjectId
        const principal =
          type === 'team' ? str(m.principalId) || null : idText(m.principalId);
        if (!principal || seen.has(`${type}:${principal}`)) {
          skipped.push({
            id: `${id}.members[${String(pos)}]`,
            reason: principal
              ? 'duplicate member'
              : 'member without a usable principalId',
          });
          return;
        }
        seen.add(`${type}:${principal}`);
        members.push({
          org_id: org,
          project_id: id,
          principal_type: type,
          principal_id: principal,
          role: m.role ?? 'viewer',
          added_by: idText(m.addedBy),
          added_at: m.addedAt ?? d.createdAt,
          pos,
        });
      },
    );
  }
  return { projects, members, skipped };
}

export async function writeProjects(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const { projects, members, skipped } = importProjectDocs(docs);
  stats.skipped.push(...skipped);
  await writePlan(
    k,
    [
      { table: 'content.projects', rows: projects },
      { table: 'content.project_members', rows: members },
    ],
    stats,
  );
  return stats;
}
