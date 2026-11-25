/**
 * Documents of the `slack_user_links` collection as rows of `integrations.slack_user_links` (ADR-SLACKID-01 D6-D8). A link is
 * its organisation's own row: it is stamped with the organisation the document names, and only when the person it names is a
 * copied user of that organisation. A link to a person who is not there (erased, or of another organisation) is reported and not
 * copied, because the bot would otherwise act as someone the organisation does not hold.
 *
 * Idempotent: rows already there are left alone (`ON CONFLICT DO NOTHING`), and the follower replaces them in place.
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

export const SLACK_USER_LINKS_TABLE = 'integrations.slack_user_links';

const str = (v: unknown): string | null =>
  typeof v === 'string' && v !== '' ? v : null;

/** The row a link document becomes, or why it cannot be one. */
export function slackUserLinkRow(d: NeutralDoc): Row | string {
  const org = idText(d.orgId) ?? str(d.orgId);
  const user = idText(d.userId) ?? str(d.userId);
  const workspace = str(d.workspaceId);
  const slackUser = str(d.slackUserId);
  const team = str(d.teamId);
  const linkedAt = str(d.linkedAt);
  if (!org) return 'Slack account link without an organisation';
  if (!user || !workspace || !slackUser || !team || !linkedAt)
    return 'Slack account link without its person, workspace, account or link time';
  if (typeof d.email !== 'string')
    return 'Slack account link without its email label';
  const created = str(d.createdAt) ?? linkedAt;
  return {
    org_id: org,
    workspace_id: workspace,
    slack_user_id: slackUser,
    team_id: team,
    user_id: user,
    email: d.email,
    slack_name: typeof d.slackName === 'string' ? d.slackName : '',
    linked_at: linkedAt,
    version: typeof d.__v === 'number' ? d.__v : 0,
    created_at: created,
    updated_at: str(d.updatedAt) ?? created,
  };
}

export async function importSlackUserLinkDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection !== 'slack_user_links')
    throw new Error(`slack-user-link does not import '${collection}'`);
  const stats = emptyStats(docs.length);
  const candidates: Array<{ id: string; row: Row }> = [];
  for (const d of docs) {
    const row = slackUserLinkRow(d);
    if (typeof row === 'string')
      stats.skipped.push({ id: String(d._id), reason: row });
    else candidates.push({ id: String(d._id), row });
  }
  const users = [...new Set(candidates.map((c) => String(c.row.user_id)))];
  const members = new Set<string>();
  if (users.length) {
    const { rows } = await k.query<{ id: string; org_id: string }>(
      '/* pipeshub:integrations.import_link_users */ SELECT id, org_id FROM identity.users WHERE id = ANY($1::text[])',
      [users],
    );
    for (const r of rows) members.add(`${r.org_id}/${r.id}`);
  }
  const rows: Row[] = [];
  for (const c of candidates) {
    if (members.has(`${String(c.row.org_id)}/${String(c.row.user_id)}`))
      rows.push(c.row);
    else
      stats.skipped.push({
        id: c.id,
        reason:
          'Slack account link whose person is not a copied user of its organisation (not linked to anyone)',
      });
  }
  await writePlan(k, [{ table: SLACK_USER_LINKS_TABLE, rows }], stats);
  return stats;
}
