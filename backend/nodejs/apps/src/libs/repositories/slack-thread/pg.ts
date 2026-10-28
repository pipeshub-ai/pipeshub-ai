/**
 * PostgreSQL implementation of the Slack thread repository (`DOC_STORE=postgres`). The bot has no session: with an org in context
 * the link is that org's, otherwise it is the install's organisation's (an extension that serves several organisations replaces
 * this repository with one that finds the organisation of a thread).
 */
import { MissingOrgContext } from '../../context/orgContext';
import { installOrg } from '../oauth-app/pg-org';
import { stripNul } from '../nul';
import { asOrg, inUnit, orgForKey } from '../pg-util';
import type { SlackThreadRepository } from './types';

export class PgSlackThreadRepository implements SlackThreadRepository {
  async getConversationId(
    threadId: string,
    botId: string,
    email: string,
  ): Promise<string | null> {
    const [thread, bot, mail] = stripNul(
      [threadId, botId, email],
      'slack-thread',
      'key',
    );
    const org = await orgForKey(installOrg);
    if (!org) return null;
    return asOrg(org, () =>
      inUnit(undefined, { write: false }, async (k) => {
        const { rows } = await k.query<{ conversation_id: string }>(
          '/* pipeshub:integrations.slack_thread_get */ SELECT conversation_id FROM integrations.slack_threads WHERE org_id = $1 AND thread_id = $2 AND bot_id = $3 AND email = $4',
          [k.orgId, thread, bot, mail],
        );
        return rows[0]?.conversation_id ?? null;
      }),
    );
  }

  async saveConversationId(row: {
    threadId: string;
    conversationId: string;
    botId: string;
    email: string;
  }): Promise<void> {
    const r = stripNul(row, 'slack-thread', 'row');
    const org = await orgForKey(installOrg);
    if (!org)
      throw new MissingOrgContext(
        'a Slack thread is saved for an organisation: the install has none yet',
      );
    await asOrg(org, () =>
      inUnit(undefined, { write: true }, async (k) => {
        const now = new Date();
        await k.query(
          `/* pipeshub:integrations.slack_thread_save */ INSERT INTO integrations.slack_threads (org_id, thread_id, bot_id, email, conversation_id, created_at, updated_at)
             VALUES ($1, $2, $3, $4, $5, $6, $6)
             ON CONFLICT (org_id, thread_id, bot_id, email) DO UPDATE SET conversation_id = EXCLUDED.conversation_id, updated_at = EXCLUDED.updated_at`,
          [k.orgId, r.threadId, r.botId, r.email, r.conversationId, now],
        );
      }),
    );
  }
}
