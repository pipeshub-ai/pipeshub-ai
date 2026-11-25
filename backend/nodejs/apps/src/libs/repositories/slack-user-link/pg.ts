/**
 * PostgreSQL implementation of the Slack account links (`DOC_STORE=postgres`). Every call names its organisation; the row
 * policy holds it to that organisation, as the Mongo key does.
 */
import { inOrgUnit } from '../oauth-app/pg-org';
import { stripNul } from '../strip-nul';
import type { Importable, ImportStats, NeutralDoc } from '../import';
import { kernelTx } from '../pg';
import type { Tx } from '../uow';
import { importSlackUserLinkDocs } from './pg-import';
import type {
  NewSlackUserLink,
  SlackUserLink,
  SlackUserLinkRepository,
} from './types';

interface Row {
  org_id: string;
  workspace_id: string;
  slack_user_id: string;
  team_id: string;
  user_id: string;
  email: string;
  slack_name: string;
  linked_at: Date;
}

const plain = (r: Row): SlackUserLink => ({
  orgId: r.org_id,
  workspaceId: r.workspace_id,
  slackUserId: r.slack_user_id,
  teamId: r.team_id,
  userId: r.user_id,
  email: r.email,
  slackName: r.slack_name,
  linkedAt: new Date(r.linked_at),
});

export class PgSlackUserLinkRepository
  implements SlackUserLinkRepository, Importable
{
  /** The migration copy: documents as they were stored (see ./pg-import.ts). */
  importBatch(
    collection: string,
    docs: NeutralDoc[],
    tx: Tx,
  ): Promise<ImportStats> {
    return importSlackUserLinkDocs(kernelTx(tx), collection, docs);
  }

  async get(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<SlackUserLink | null> {
    const [o, ws, su] = stripNul(
      [orgId, workspaceId, slackUserId],
      'slack-user-link',
      'key',
    );
    return inOrgUnit(o, undefined, false, async (k) => {
      const { rows } = await k.query<Row>(
        '/* pipeshub:integrations.slack_user_link_get */ SELECT * FROM integrations.slack_user_links WHERE org_id = $1 AND workspace_id = $2 AND slack_user_id = $3',
        [k.orgId, ws, su],
      );
      return rows[0] ? plain(rows[0]) : null;
    });
  }

  async upsert(link: NewSlackUserLink): Promise<SlackUserLink> {
    const r = stripNul(link, 'slack-user-link', 'row');
    return inOrgUnit(r.orgId, undefined, true, async (k) => {
      const now = new Date();
      await k.query(
        `/* pipeshub:integrations.slack_user_link_save */ INSERT INTO integrations.slack_user_links
           (org_id, workspace_id, slack_user_id, team_id, user_id, email, slack_name, linked_at, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, $8)
           ON CONFLICT (org_id, workspace_id, slack_user_id) DO UPDATE SET team_id = EXCLUDED.team_id,
             user_id = EXCLUDED.user_id, email = EXCLUDED.email, slack_name = EXCLUDED.slack_name,
             linked_at = EXCLUDED.linked_at, updated_at = EXCLUDED.updated_at, version = integrations.slack_user_links.version + 1`,
        [
          k.orgId,
          r.workspaceId,
          r.slackUserId,
          r.teamId,
          r.userId,
          r.email,
          r.slackName,
          now,
        ],
      );
      return { ...link, linkedAt: now };
    });
  }

  async remove(
    orgId: string,
    workspaceId: string,
    slackUserId: string,
  ): Promise<void> {
    const [o, ws, su] = stripNul(
      [orgId, workspaceId, slackUserId],
      'slack-user-link',
      'key',
    );
    await inOrgUnit(o, undefined, true, async (k) => {
      await k.query(
        '/* pipeshub:integrations.slack_user_link_remove */ DELETE FROM integrations.slack_user_links WHERE org_id = $1 AND workspace_id = $2 AND slack_user_id = $3',
        [k.orgId, ws, su],
      );
    });
  }

  async listForOrg(orgId: string): Promise<SlackUserLink[]> {
    const [o] = stripNul([orgId], 'slack-user-link', 'key');
    return inOrgUnit(o, undefined, false, async (k) => {
      const { rows } = await k.query<Row>(
        '/* pipeshub:integrations.slack_user_link_list */ SELECT * FROM integrations.slack_user_links WHERE org_id = $1 ORDER BY linked_at',
        [k.orgId],
      );
      return rows.map(plain);
    });
  }

  async removeForUser(orgId: string, userId: string): Promise<number> {
    const [o, u] = stripNul([orgId, userId], 'slack-user-link', 'key');
    return inOrgUnit(o, undefined, true, async (k) => {
      const { rowCount } = await k.query(
        '/* pipeshub:integrations.slack_user_link_remove_user */ DELETE FROM integrations.slack_user_links WHERE org_id = $1 AND user_id = $2',
        [k.orgId, u],
      );
      return rowCount ?? 0;
    });
  }

  async removeForOrg(orgId: string): Promise<number> {
    const [o] = stripNul([orgId], 'slack-user-link', 'key');
    return inOrgUnit(o, undefined, true, async (k) => {
      const { rowCount } = await k.query(
        '/* pipeshub:integrations.slack_user_link_remove_org */ DELETE FROM integrations.slack_user_links WHERE org_id = $1',
        [k.orgId],
      );
      return rowCount ?? 0;
    });
  }
}
