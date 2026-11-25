/**
 * The approver directory on PostgreSQL: `HarnessApproverDirectory` over main's users in `identity.*`, read through
 * main's repositories (`PgApproverPeople`). The directory contract, and the control plane's group membership over the
 * same rows.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { HarnessApproverDirectory } from '../../../src/libs/repositories/agent-harness/approver-directory/mongo';
import { PgApproverPeople } from '../../../src/libs/repositories/agent-harness/approver-directory/pg';
import { RepositoryGroupMembership } from '../../../src/libs/repositories/control-plane/user-directory/pg';
import {
  ApproverOrgSeed,
  describeApproverDirectoryContract,
} from './support/approver-directory-contract';
import {
  boundStore,
  PgStack,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
} from './support/harness-pg';
import { usePgIdentityRepositories } from './support/harness-pg-identity';

const ORG_FIRST = {
  isApprover: 0,
  isOrgAdmin: 0,
  isMember: 0,
};

for (const bin of pgBins()) {
  pgSuite(
    `agent harness approver directory on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      let restore: () => void;
      let docStore: string | undefined;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_approver_directory');
        restore = usePgIdentityRepositories();
        // The org-policy fence the directory takes first picks its store by DOC_STORE.
        docStore = process.env.DOC_STORE;
        process.env.DOC_STORE = 'postgres';
      });
      after(async () => {
        if (docStore === undefined) delete process.env.DOC_STORE;
        else process.env.DOC_STORE = docStore;
        restore?.();
        await stack?.stop();
      });

      const seed = async (orgs: ApproverOrgSeed[]): Promise<void> => {
        const now = new Date();
        await stack.admin(async (c) => {
          await c.query(
            'TRUNCATE identity.orgs, identity.users, identity.user_groups CASCADE',
          );
          for (const org of orgs) {
            if (!/^[0-9a-f]{24}$/.test(org.orgId)) continue;
            await c.query(
              `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at)
              VALUES ($1, $2, $3, 'business', $4, $4)`,
              [
                org.orgId,
                `${org.orgId}.example.com`,
                `owner@${org.orgId}.example.com`,
                now,
              ],
            );
            for (const user of org.users ?? []) {
              await c.query(
                `INSERT INTO identity.users (org_id, id, slug, email, role, is_deleted, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $7)`,
                [
                  org.orgId,
                  user.userId,
                  `u-${user.userId}`,
                  `${user.userId}@example.com`,
                  user.admin ? 'admin' : 'member',
                  user.deleted === true,
                  now,
                ],
              );
            }
            for (const group of org.groups ?? []) {
              await c.query(
                `INSERT INTO identity.user_groups (org_id, id, slug, name, type, is_deleted, created_at, updated_at)
                VALUES ($1, $2, $3, $4, 'custom', $5, $6, $6)`,
                [
                  org.orgId,
                  group.groupId,
                  `g-${group.groupId}`,
                  `group-${group.groupId}`,
                  group.deleted === true,
                  now,
                ],
              );
              for (const [pos, member] of group.members.entries()) {
                await c.query(
                  `INSERT INTO identity.user_group_members (org_id, group_id, user_id, pos) VALUES ($1, $2, $3, $4)`,
                  [org.orgId, group.groupId, member, pos],
                );
              }
            }
          }
        });
      };

      const directoryOver = async (
        orgs: ApproverOrgSeed[],
      ): Promise<HarnessApproverDirectory> => {
        await resetHarnessPg(stack);
        await seed(orgs);
        return new HarnessApproverDirectory(new PgApproverPeople());
      };
      const scopedDirectory = async (orgs: ApproverOrgSeed[]) =>
        scopedStore(await directoryOver(orgs), ORG_FIRST);

      describeApproverDirectoryContract(
        'HarnessApproverDirectory over PgApproverPeople',
        scopedDirectory,
      );

      // DOS-MONGO-FU-1: the control plane's group membership, which reads the `identity.*` rows the directory reads.
      describe("the control plane's group membership on PostgreSQL", () => {
        const ORG = '0000000000000000000000b1';
        const OTHER = '0000000000000000000000b2';
        const ANA = '0000000000000000000000b3';
        const BEN = '0000000000000000000000b4';
        const LEFT = '0000000000000000000000b5';
        const THEIRS = '0000000000000000000000b6';
        const LIVE = '0000000000000000000000c1';
        const GONE = '0000000000000000000000c2';
        const FOREIGN = '0000000000000000000000c3';
        beforeEach(() =>
          directoryOver([
            {
              orgId: ORG,
              users: [{ userId: ANA }, { userId: BEN }, { userId: LEFT, deleted: true }],
              groups: [
                { groupId: LIVE, members: [ANA] },
                { groupId: GONE, members: [BEN], deleted: true },
              ],
            },
            {
              orgId: OTHER,
              users: [{ userId: THEIRS }],
              groups: [{ groupId: FOREIGN, members: [THEIRS] }],
            },
          ]),
        );

        it("the control plane's group membership: a live group of the org that holds the user, nothing else", async () => {
          const membership = boundStore(new RepositoryGroupMembership());
          expect(await membership.isMember(ORG, LIVE, ANA)).to.equal(true);
          expect(await membership.isMember(ORG, LIVE, BEN)).to.equal(false);
          expect(await membership.isMember(ORG, GONE, BEN)).to.equal(false);
          expect(await membership.isMember(ORG, FOREIGN, THEIRS)).to.equal(false);
          expect(await membership.isMember(ORG, 'bad', ANA)).to.equal(false);
        });
      });
    },
  );
}
