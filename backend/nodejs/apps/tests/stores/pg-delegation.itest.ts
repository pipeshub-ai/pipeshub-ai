/**
 * The delegation repository on PostgreSQL (Alembic head, row-level security, through PgBouncer): the contract the Mongo store meets,
 * plus what only PostgreSQL shows: a row of another org is invisible, an id that is not an id matches nothing, and a call made with
 * another org's context is refused.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { PgDelegationRepository } from '../../src/libs/repositories/delegation/pg';
import type { DelegationRepository } from '../../src/libs/repositories/delegation';
import { describeDelegationContract } from './contract/delegation.contract';
import { PG_BINS, PgStack, haveStack, inOrg, newId, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL delegation repository (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    before(async () => {
      stack = await startPgStack(bin, 'delegation_stores');
    });
    after(async () => {
      await stack?.stop();
    });

    const raw = new PgDelegationRepository();
    /** Every call runs in the org its first argument names (the org the caller is acting for). */
    const repo = new Proxy(raw, {
      get(target, prop, receiver) {
        const v = Reflect.get(target, prop, receiver) as unknown;
        if (typeof v !== 'function' || typeof prop !== 'string') return v;
        return (...args: unknown[]) => {
          const first = args[0];
          const org = typeof first === 'string' ? first : (first as { orgId: string }).orgId;
          return inOrg(org, () => (v as (...a: unknown[]) => unknown).apply(target, args));
        };
      },
    }) as DelegationRepository;

    describeDelegationContract({
      repo,
      newId,
      setEventCreatedAt: (id, at) =>
        stack.admin(async (c) => { await c.query('UPDATE oauth.delegation_events SET created_at = $2 WHERE id = $1', [id, at]) }),
    });

    describe('what only PostgreSQL shows', () => {
      it('hides the rows of another org even from a statement that names them', async () => {
        const orgA = newId(), orgB = newId(), sa = newId(), user = newId();
        await repo.grant({ orgId: orgA, serviceAccountId: sa, subjectUserId: user, grantedBy: newId() });
        const seenByB = await inOrg(orgB, () =>
          new PgUnitOfWork().run(
            async (t) =>
              (await kernelTx(t).query<{ n: number }>('SELECT count(*)::int AS n FROM oauth.service_account_delegations')).rows[0]!.n,
            { readOnly: true },
          ),
        );
        expect(seenByB).to.equal(0);
      });

      it('refuses a call whose org differs from the one the unit runs as', async () => {
        const orgA = newId(), orgB = newId();
        let failure: Error | undefined;
        try {
          await inOrg(orgA, () => raw.isAllowed(orgB, newId(), newId()));
        } catch (e) {
          failure = e as Error;
        }
        expect(failure?.message).to.match(/organisation of the call differs/);
      });

      it('matches nothing for an id that is not an id', async () => {
        const org = newId();
        expect(await repo.isAllowed(org, 'not-an-id', newId())).to.equal(false);
        expect(await repo.revoke(org, 'not-an-id', 'x')).to.equal(false);
        expect(await repo.listFor(org, 'nope')).to.deep.equal([]);
      });
    });
  });
}
