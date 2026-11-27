/**
 * Shared setup for the harness tests that need a real PostgreSQL (`*.pg.itest.ts`): migrated to Alembic head (the
 * `harness` branch included), row-level security on, behind PgBouncer in transaction mode, as main's pg-runtime CI
 * runs it. Needs PG_TEST_BINS, PG_TEST_PGBOUNCER and PG_TEST_MIGRATE_PYTHON (see tests/libs/db/pg-harness.ts);
 * without them the suites skip.
 *
 * The contract suites call stores without an organisation context, as the Mongo ones do; on PostgreSQL a unit runs as
 * one organisation, so `scopedStore` runs each call as the organisation its arguments name, and `transactAs` opens the
 * durable transaction as the organisation the test is for.
 */
import { randomBytes } from 'crypto';
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext';
import { inPgDurableTransaction } from '../../../../src/libs/repositories/agent-harness/durable-transaction/pg';
import type { Tx } from '../../../../src/libs/repositories/uow';
import {
  OrgArgs,
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  scoped,
  startPgStack,
} from '../../../stores/pg-store-harness';

export { PG_BINS, haveStack, inOrg };
export type { PgStack };

/** A fresh org id in the shape every store takes. */
export const hexOrg = (): string => randomBytes(12).toString('hex');

/** One migrated database and pooler per suite; the jobs login is on, for the cross-org reads. */
export const startHarnessPg = (bin: string, db: string): Promise<PgStack> =>
  startPgStack(bin, db, { jobs: true });

const ledger = new OrgLedger();

/** The store, with each call run as the organisation its arguments name. */
export const scopedStore = <T extends object>(store: T, orgArgs: OrgArgs = {}): T =>
  scoped(store, ledger, orgArgs);

/** The durable transaction as `orgId`. */
export const transactAs = <T>(work: (tx: Tx) => Promise<T>, orgId: string): Promise<T> =>
  inOrg(orgId, () => inPgDurableTransaction(work));

/** Empties every harness table (a superuser session, outside the row policies). */
export async function resetHarnessPg(stack: PgStack): Promise<void> {
  await stack.admin(async (c) => {
    const { rows } = await c.query<{ name: string }>(
      `SELECT format('%I.%I', schemaname, tablename) AS name FROM pg_tables WHERE schemaname = 'harness'`,
    );
    if (rows.length > 0) await c.query(`TRUNCATE ${rows.map((r) => r.name).join(', ')}`);
    await c.query(`DELETE FROM platform.outbox`);
  });
}

/** Mocha's `describe` when the PostgreSQL stack is configured, else `describe.skip`. */
export const pgSuite = haveStack ? describe : describe.skip;
/** The bin directories to run against; one placeholder when none is set, so a skipped suite still lists. */
export const pgBins = (): string[] => (PG_BINS.length ? PG_BINS : ['none']);

const HEX24 = /^[0-9a-f]{24}$/;

/** The org a call names in its first argument: an org id, or an object holding one (a scope, a job, a record). */
const firstArgOrg = (args: readonly unknown[]): string | undefined => {
  const first = args[0];
  if (typeof first === 'string') return HEX24.test(first) ? first : undefined;
  const named = (first as { orgId?: unknown } | null | undefined)?.orgId;
  return typeof named === 'string' && HEX24.test(named) ? named : undefined;
};

/**
 * The store, each call run as the org its first argument names, as an entry point binds it (ADR-SEC-02): the suite is
 * the entry point here. A call that names no org (a malformed id, a cross-org read) runs with whatever is bound.
 */
export const boundStore = <T extends object>(
  store: T,
  opts: { unbound?: readonly string[] } = {},
): T =>
  new Proxy(store, {
    get(target, prop, receiver) {
      const v = Reflect.get(target, prop, receiver) as unknown;
      // `unbound`: the calls an entry point makes before any org is bound (a sign-in lookup by account).
      if (typeof v !== 'function' || opts.unbound?.includes(String(prop))) return v;
      return (...args: unknown[]) => {
        const org = firstArgOrg(args);
        const call = () => (v as (...a: unknown[]) => unknown).apply(target, args);
        if (org === undefined || getCurrentOrgId() === org) return call();
        try {
          return inOrg(org, call);
        } catch (error) {
          // A call bound to another org is refused as the store would refuse it: a rejected promise.
          return Promise.reject(error as Error);
        }
      };
    },
  });
