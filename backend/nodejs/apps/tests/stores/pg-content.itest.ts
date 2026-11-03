/**
 * The document, project, connectors-config and Slack thread repositories on PostgreSQL (Alembic head, row-level security,
 * through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgConnectorsConfigRepository } from '../../src/libs/repositories/connectors-config/pg';
import { PgDocumentRepository } from '../../src/libs/repositories/document/pg';
import { PgProjectRepository } from '../../src/libs/repositories/project/pg';
import { PgSlackThreadRepository } from '../../src/libs/repositories/slack-thread/pg';
import type { DocumentRepository } from '../../src/libs/repositories/document';
import { describeDocumentContract } from './contract/document.contract';
import {
  describeRestContract,
  type RestFixture,
} from './contract/rest.contract';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  scoped,
  scopedUow,
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
    beginWrite: 0,
    renewWrite: 0,
    endWrite: 0,
    bumpSeq: 0,
    adoptDirectUpload: 0,
    correctSize: 0,
    markAwaitingDirectUpload: 0,
    getWithFingerprintState: 0,
    peekWriteState: 0,
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

/**
 * Mocha's `describe`, registering only the named blocks of the rest contract. The others belong to other
 * repositories and run in their own suites (organisations, metrics and lookups in pg-identity, delegation in
 * pg-delegation); they are left out rather than registered as skipped, because the workflow fails a run that
 * holds a skipped test.
 */
function onlyBlocks(names: string[], register: () => void): void {
  const g = globalThis as unknown as { describe: typeof describe };
  const real = g.describe;
  const filtered = ((name: string, fn: () => void) =>
    (names.includes(name) ? real(name, fn) : undefined) as ReturnType<typeof describe>) as typeof describe;
  g.describe = Object.assign(filtered, { skip: real.skip, only: real.only });
  try {
    register();
  } finally {
    g.describe = real;
  }
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
        // a superuser session, outside the row policies, as a raw statement against the table
        setHiddenState: (orgId, id, state) =>
          stack.admin(async (c) => {
            const r = await c.query(
              'UPDATE content.documents SET write_seq = $3, writers = $4::jsonb WHERE org_id = $1 AND id = $2',
              [orgId, id, state.writeSeq, JSON.stringify(state.writers)],
            );
            if (r.rowCount !== 1) throw new Error(`no row ${id} to set the write state on`);
          }),
        getHiddenState: (orgId, id) =>
          stack.admin(async (c) => {
            const r = await c.query<{ write_seq: string; writers: { t: string; until: number }[] }>(
              'SELECT write_seq, writers FROM content.documents WHERE org_id = $1 AND id = $2',
              [orgId, id],
            );
            const row = r.rows[0];
            if (!row) throw new Error(`no row ${id} to read the write state from`);
            return { writeSeq: Number(row.write_seq), writers: row.writers };
          }),
        rowStamp: (orgId, id) =>
          stack.admin(async (c) => {
            const r = await c.query<{ x: string }>(
              'SELECT xmin::text AS x FROM content.documents WHERE org_id = $1 AND id = $2',
              [orgId, id],
            );
            return r.rows[0]?.x ?? '';
          }),
        // a unit of work the caller holds open (and rolls back), as a service method running inside one would
        inCallerUnit: async (orgId, body) => {
          const rollback = new Error('rollback the caller unit');
          try {
            await scopedUow().run(
              async () => {
                await body();
                throw rollback;
              },
              { org: orgId },
            );
          } catch (e) {
            if (e !== rollback) throw e;
          }
        },
      });
    });

    describe('Project, connectors config and Slack thread contract', () => {
      onlyBlocks(['project', 'connectors config', 'slack thread'], () =>
        describeRestContract({
          projects: scoped(new PgProjectRepository(), ledger, {
            create: 0,
            list: 0,
            listWithLinkedKbForMember: 0,
            removeUserFromAll: 0,
            listAccessibleIds: 0,
          }),
          connectors: scoped(new PgConnectorsConfigRepository(), ledger, {
            find: 0,
            enable: 0,
            createEnabled: 0,
          }),
          slack: scoped(new PgSlackThreadRepository(), ledger),
          newId,
        } as unknown as RestFixture),
      );
    });
  });
}
