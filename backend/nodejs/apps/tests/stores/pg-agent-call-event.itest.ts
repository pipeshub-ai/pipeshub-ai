/**
 * The agent call repository on PostgreSQL (Alembic head, row-level security, through PgBouncer): the contract the Mongo store meets,
 * plus what only PostgreSQL shows: a row of another org is invisible to a statement that names it, and a call made with another
 * org's context is refused.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { PgAgentCallRepository } from '../../src/libs/repositories/agent-call-event/pg';
import type { AgentCallRepository } from '../../src/libs/repositories/agent-call-event';
import { describeAgentCallContract } from './contract/agent-call-event.contract';
import { PG_BINS, PgStack, haveStack, inOrg, newId, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL agent call repository (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    before(async () => {
      stack = await startPgStack(bin, 'agent_call_stores');
    });
    after(async () => {
      await stack?.stop();
    });

    const raw = new PgAgentCallRepository();
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
    }) as AgentCallRepository;

    describeAgentCallContract({ repo, newId });

    describe('what only PostgreSQL shows', () => {
      const row = (org: string) => ({
        orgId: org, userId: newId(), tokenId: 'jti-1', tokenKind: 'pat' as const, method: 'POST', route: '/x',
        statusCode: 200, durationMs: 1, scopeDenied: false, rollup: false,
      });

      it('hides the rows of another org even from a statement that names them', async () => {
        const orgA = newId(), orgB = newId();
        await repo.record(row(orgA));
        const seenByB = await inOrg(orgB, () =>
          new PgUnitOfWork().run(
            async (t) => (await kernelTx(t).query<{ n: number }>('SELECT count(*)::int AS n FROM oauth.agent_call_events')).rows[0]!.n,
            { readOnly: true },
          ),
        );
        expect(seenByB).to.equal(0);
      });

      it('refuses a call whose org differs from the one the unit runs as', async () => {
        let failure: Error | undefined;
        try {
          await inOrg(newId(), () => raw.record(row(newId())));
        } catch (e) {
          failure = e as Error;
        }
        expect(failure?.message).to.match(/organisation of the call differs/);
      });
    });
  });
}
