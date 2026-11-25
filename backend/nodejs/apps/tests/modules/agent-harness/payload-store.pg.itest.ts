/**
 * The PayloadStore, PayloadReferences and TransactionalPayloadStore contracts on PostgreSQL
 * (`harness.journal_payloads`, Alembic head, row-level security, PgBouncer in transaction mode), on the terms the
 * replica set runs them: a mark or a put in the caller's unit, and an aborted unit leaves neither.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import {
  PgPayloadStore,
  pgPayloadId,
} from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import type { Tx } from '../../../src/libs/repositories/uow';
import {
  describePayloadReferencesContract,
  describePayloadStoreContract,
  describeTransactionalPayloadStoreContract,
} from './support/payload-store-contract';
import {
  PgStack,
  boundStore,
  pgBins,
  pgSuite,
  resetHarnessPg,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

for (const bin of pgBins()) {
  pgSuite(
    `agent harness journal payloads on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_payloads');
      });
      after(async () => {
        await stack?.stop();
      });

      const row = (orgId: string, ref: string): [string, string] => [
        orgId,
        pgPayloadId(orgId, ref) ?? '',
      ];

      describePayloadStoreContract(
        'PgPayloadStore',
        async () => {
          await resetHarnessPg(stack);
          return {
            store: boundStore(new PgPayloadStore()),
            remove: async (orgId, ref) => {
              await stack.admin((c) =>
                c.query(
                  `DELETE FROM harness.journal_payloads WHERE org_id = $1 AND id = $2`,
                  row(orgId, ref),
                ),
              );
            },
          };
        },
        { carriedName: (ref) => ref.replace(/^pg:/, 'mongo:') },
      );

      const pg = async () => {
        await resetHarnessPg(stack);
        return {
          store: boundStore(new PgPayloadStore()),
          transact: <T>(orgId: string, work: (tx: Tx) => Promise<T>) =>
            transactAs(work, orgId),
          isReferenced: async (orgId: string, ref: string) =>
            (
              await stack.admin((c) =>
                c.query<{ referenced: boolean | null }>(
                  `SELECT referenced FROM harness.journal_payloads WHERE org_id = $1 AND id = $2`,
                  row(orgId, ref),
                ),
              )
            ).rows[0]?.referenced === true,
        };
      };
      describePayloadReferencesContract<Tx>('PgPayloadStore', pg, {
        transactional: true,
      });
      describeTransactionalPayloadStoreContract<Tx>('PgPayloadStore', pg, {
        transactional: true,
      });
    },
  );
}
