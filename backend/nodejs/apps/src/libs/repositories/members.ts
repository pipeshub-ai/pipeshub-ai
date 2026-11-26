/**
 * The member list of a shared chat or saved search, replaced as a whole the way the document store replaced its
 * `sharedWith` array: entries the caller names stay (an entry keeps its own id, given or already stored), the others go.
 * Statements are static: the two tables differ in name, not in shape.
 */
import type { Tx as PgKernelTx } from '../db';
import { newObjectId, oidOf } from './pg-util';

export interface MemberInput {
  _id?: string;
  userId: string;
  accessLevel?: 'read' | 'write';
}

const CHAT = {
  existing: `/* pipeshub:conversation.members_existing */ SELECT user_id, entry_id FROM conversation.chat_session_members
    WHERE org_id = $1 AND session_id = $2::text AND user_id = ANY($3::text[])`,
  drop: `/* pipeshub:conversation.members_drop */ DELETE FROM conversation.chat_session_members
    WHERE org_id = $1 AND session_id = $2::text AND user_id <> ALL($3::text[])`,
  set: `/* pipeshub:conversation.members_set */ INSERT INTO conversation.chat_session_members (org_id, session_id, user_id, access_level, pos, entry_id)
    SELECT $1, $2, u.user_id, u.level, u.pos, u.entry FROM unnest($3::text[], $4::text[], $5::int[], $6::text[]) AS u(user_id, level, pos, entry)
    ON CONFLICT (org_id, session_id, user_id) DO UPDATE SET access_level = EXCLUDED.access_level, pos = EXCLUDED.pos, entry_id = EXCLUDED.entry_id`,
};
const SEARCH = {
  existing: `/* pipeshub:search.members_existing */ SELECT user_id, entry_id FROM conversation.semantic_search_members
    WHERE org_id = $1 AND search_id = $2::text AND user_id = ANY($3::text[])`,
  drop: `/* pipeshub:search.members_drop */ DELETE FROM conversation.semantic_search_members
    WHERE org_id = $1 AND search_id = $2::text AND user_id <> ALL($3::text[])`,
  set: `/* pipeshub:search.members_set */ INSERT INTO conversation.semantic_search_members (org_id, search_id, user_id, access_level, pos, entry_id)
    SELECT $1, $2, u.user_id, u.level, u.pos, u.entry FROM unnest($3::text[], $4::text[], $5::int[], $6::text[]) AS u(user_id, level, pos, entry)
    ON CONFLICT (org_id, search_id, user_id) DO UPDATE SET access_level = EXCLUDED.access_level, pos = EXCLUDED.pos, entry_id = EXCLUDED.entry_id`,
};

export async function replaceMembers(
  k: PgKernelTx,
  kind: 'chat' | 'search',
  parentId: string,
  list: MemberInput[],
): Promise<void> {
  const sql = kind === 'chat' ? CHAT : SEARCH;
  // one entry per user, the last one named wins (the table cannot hold two)
  const byUser = new Map(list.map((m) => [m.userId, m]));
  const members = [...byUser.values()];
  const users = members.map((m) => m.userId);
  const stored = new Map(
    (
      await k.query<{ user_id: string; entry_id: string }>(sql.existing, [
        k.orgId,
        parentId,
        users,
      ])
    ).rows.map((r) => [r.user_id, r.entry_id]),
  );
  await k.query(sql.drop, [k.orgId, parentId, users]);
  if (!members.length) return;
  await k.query(sql.set, [
    k.orgId,
    parentId,
    users,
    members.map((m) => m.accessLevel ?? 'read'),
    members.map((_, i) => i),
    members.map(
      (m) => oidOf(m._id ?? '') ?? stored.get(m.userId) ?? newObjectId(),
    ),
  ]);
}
