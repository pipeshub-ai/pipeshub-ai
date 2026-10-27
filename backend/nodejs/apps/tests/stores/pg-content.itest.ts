/**
 * The document repository on PostgreSQL (Alembic head, row-level security,
 * through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgDocumentRepository } from '../../src/libs/repositories/document/pg';
import type { DocumentRepository } from '../../src/libs/repositories/document';
import { describeDocumentContract } from './contract/document.contract';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  scoped,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

/** A scoped document repository whose stream runs each step as the organisation it was asked for. */
function scopedDocuments(
  real: DocumentRepository,
  ledger: OrgLedger,
): DocumentRepository {
  const base = scoped(real, ledger, {
    get: 0,
    getLive: 0,
    update: 0,
    retire: 0,
    remove: 1,
    claimArtifactSetForDelete: 0,
    renewArtifactSetClaim: 0,
    removeClaimedArtifactSet: 1,
    removeAwaitingDirectUpload: 0,
    clearAwaitingDirectUpload: 0,
    listByVirtualRecord: 0,
    listForRelocate: 0,
    listByFolders: 0,
    findExistingIds: 0,
    subtree: 0,
    pageByConnector: 0,
    deleteByConnector: 0,
  });
  return new Proxy(base, {
    get(target, prop, receiver) {
      if (prop !== 'streamTaggedOutsidePrefixes')
        return Reflect.get(target, prop, receiver) as unknown;
      return (
        orgId: string,
        scope: Parameters<DocumentRepository['streamTaggedOutsidePrefixes']>[1],
      ) =>
        (async function* () {
          const it = inOrg(orgId, () =>
            real
              .streamTaggedOutsidePrefixes(orgId, scope)
              [Symbol.asyncIterator](),
          );
          for (;;) {
            const next = await inOrg(orgId, () => it.next());
            if (next.done) return;
            yield next.value;
          }
        })();
    },
  });
}

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL content stores (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const ledger = new OrgLedger();

    before(async () => {
      stack = await startPgStack(bin, 'content_stores');
    });
    after(async () => {
      await stack?.stop();
    });

    describe('DocumentRepository contract', () => {
      describeDocumentContract({
        repo: scopedDocuments(new PgDocumentRepository(), ledger),
        newId,
      });
    });
  });
}
