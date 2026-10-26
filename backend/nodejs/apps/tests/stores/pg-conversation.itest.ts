/**
 * The conversation, saved-search and access-request repositories on PostgreSQL (Alembic head, row-level security,
 * through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgAccessRequestRepository } from '../../src/libs/repositories/access-request/pg';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import { describeAccessRequestContract } from './contract/access-request.contract';
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
          findIdsByOwnTurnContent: 0, listVisibleTitles: 0, appendMessages: 1, replaceMessage: 1, deleteMessages: 1,
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
          stack.admin(async (c) => void (await c.query('UPDATE conversation.semantic_searches SET created_at = $2 WHERE id = $1', [id, at]))),
      });
    });

    describe('AccessRequestRepository contract', () => {
      describeAccessRequestContract({
        repo: scoped(new PgAccessRequestRepository(), ledger, {
          create: 0, findOpen: 0, countSince: 0, listForApprover: 0, getForApprover: 0, get: 0, decide: 0,
        }),
        uow: scopedUow(),
        newId,
        rawKeys: (id) =>
          stack.admin(async (c) => {
            const r = (await c.query('SELECT * FROM access.access_requests WHERE id = $1', [id])).rows[0];
            const keys = ['_id', 'orgId', 'requesterId', 'recordId', 'kind', 'ownerUserIds', 'status', 'createdAt', 'updatedAt', '__v'];
            const optional: Array<[string, string]> = [
              ['record_name', 'recordName'], ['collection_id', 'collectionId'], ['collection_name', 'collectionName'],
              ['decided_by', 'decidedBy'], ['decided_at', 'decidedAt'],
            ];
            for (const [col, key] of optional) if (r[col] !== null) keys.push(key);
            return keys;
          }),
        setCreatedAt: (id, at) =>
          stack.admin(async (c) => void (await c.query('UPDATE access.access_requests SET created_at = $2 WHERE id = $1', [id, at]))),
      });
    });
  });
}
