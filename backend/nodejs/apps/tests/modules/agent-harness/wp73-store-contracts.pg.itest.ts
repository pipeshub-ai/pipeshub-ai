/**
 * WP-73's store ports on PostgreSQL (C-2): the actor's membership of main's user groups (`identity.user_groups`, read through main's group repository), held to the same
 * suites as their in-memory and Mongo adapters. The audit run directory's runs on PostgreSQL in
 * harness-directory.pg.itest.ts.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { PgGroupMembership } from '../../../src/libs/repositories/agent-harness/group-membership/pg';
import {
  describeActorGroupMembershipContract,
} from './support/wp73-stores-contract';
import {
  PgStack,
  boundStore,
  pgBins,
  pgSuite,
  resetHarnessPg,
  startHarnessPg,
} from './support/harness-pg';
import { usePgIdentityRepositories } from './support/harness-pg-identity';

for (const bin of pgBins()) {
  pgSuite(
    `WP-73 store contracts on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      let restore: () => void;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_wp73');
        restore = usePgIdentityRepositories();
      });
      after(async () => {
        restore?.();
        await stack?.stop();
      });

      describeActorGroupMembershipContract('postgres', async () => {
        await resetHarnessPg(stack);
        await stack.admin((c) =>
          c.query('TRUNCATE identity.user_groups CASCADE'),
        );
        return {
          membership: boundStore(new PgGroupMembership()),
          group: async (orgId, groupId, users) => {
            await stack.admin(async (c) => {
              await c.query(
                `INSERT INTO identity.user_groups (org_id, id, slug, name, type, created_at, updated_at)
                VALUES ($1, $2, $3, $3, 'custom', now(), now())`,
                [orgId, groupId, `group-${groupId}`],
              );
              for (const [pos, user] of users.entries()) {
                await c.query(
                  `INSERT INTO identity.user_group_members (org_id, group_id, user_id, pos) VALUES ($1, $2, $3, $4)`,
                  [orgId, groupId, user, pos],
                );
              }
            });
          },
          removeFrom: async (orgId, groupId, userId) => {
            await stack.admin((c) =>
              c.query(
                `DELETE FROM identity.user_group_members WHERE org_id = $1 AND group_id = $2 AND user_id = $3`,
                [orgId, groupId, userId],
              ),
            );
          },
        };
      });
    },
  );
}
