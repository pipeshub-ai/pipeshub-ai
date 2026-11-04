/**
 * The conversation and saved-search repositories on PostgreSQL (Alembic head, row-level security,
 * through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import { describeConversationContract } from './contract/conversation.contract';
import { describeSemanticSearchContract } from './contract/semantic-search.contract';
import { OrgLedger, PG_BINS, PgStack, haveStack, newId, scoped, scopedUow, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL conversation stores (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const ledger = new OrgLedger();

    before(async () => {
      stack = await startPgStack(bin);
    });
    after(async () => {
      await stack?.stop();
    });

    describe('ConversationRepository contract', () => {
      describeConversationContract({
        repo: scoped(new PgConversationRepository(), ledger, {
          findIdsByContent: 0, listArtifactMarkerContents: 1, updateUserQueryContent: 1, listVisibleSummaries: 0, appendMessages: 1, replaceMessage: 1, deleteMessages: 1,
          appendFeedback: 1, findMessage: 1, countMessages: 1, getCitations: 1,
        }),
        uow: scopedUow(),
        transactional: true,
        stripsNul: true,
        newId,
      });
    });

    describe('SemanticSearchRepository contract', () => {
      describeSemanticSearchContract({
        repo: scoped(new PgSemanticSearchRepository(), ledger, {
          findVisible: 0, listVisible: 0, unarchive: 0, delete: 0, deleteVisible: 0, findCitations: 0,
        }),
        uow: scopedUow(),
        newId,
        countHits: (ids) =>
          stack.admin(async (c) => Number((await c.query('SELECT count(*)::int AS n FROM conversation.citations WHERE id = ANY($1::text[])', [ids])).rows[0].n)),
        setCreatedAt: (id, at) =>
          stack.admin(async (c) => { await c.query('UPDATE conversation.semantic_searches SET created_at = $2 WHERE id = $1', [id, at]) }),
      });
    });
  });
}
