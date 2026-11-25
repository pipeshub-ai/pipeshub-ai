/**
 * ADR-UI-01 D2 on PostgreSQL: the seen marks on `conversation.chat_sessions.harness_seen` (conversation_0004), through
 * the conversation repository, as the org each call names (Alembic head, row-level security, PgBouncer).
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/conversation-seen.pg.itest.ts
 */
import 'reflect-metadata';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
} from '../../../src/libs/repositories/conversation';
import { conversationSeenMarks } from '../../../src/libs/repositories/agent-harness/conversation-seen';
import { describeConversationSeenContract } from './support/conversation-seen-contract';
import { PgStack, boundStore, inOrg, pgBins, pgSuite, startHarnessPg } from './support/harness-pg';

for (const bin of pgBins()) {
  pgSuite(`conversation seen marks on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_conversation_seen');
      setConversationRepository(new PgConversationRepository());
    });
    after(async () => {
      setConversationRepository();
      await stack?.stop();
    });

    describeConversationSeenContract('chat_sessions.harness_seen', () => ({
      marks: boundStore(conversationSeenMarks()),
      conversation: (orgId, userId) =>
        inOrg(orgId, async () =>
          (
            await getConversationRepository().createSession({
              orgId,
              userId,
              initiator: userId,
              title: 'seen',
              lastActivityAt: Date.now(),
              status: 'Complete',
              sessionType: 'chat',
            })
          )._id,
        ),
      updatedAt: (orgId, sessionId) =>
        inOrg(orgId, async () => {
          const row = await getConversationRepository().findHarnessSession(sessionId, orgId);
          return new Date(row?.session.updatedAt ?? 0).getTime();
        }),
      reset: async () => {
        await stack.admin((c) => c.query('TRUNCATE conversation.chat_sessions CASCADE'));
      },
    }));
  });
}
