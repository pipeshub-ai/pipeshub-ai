/**
 * Who besides a run's actor may decide its approvals (P2.6c): the
 * `ApproverDirectory` here, in memory (`approver-directory.test.ts`) and over
 * the org's users and teams on MongoDB and PostgreSQL (the itests). Its
 * admins deny; nobody but the run's actor approves.
 */
import { expect } from 'chai';
import { ApproverDirectory } from '../../../../src/modules/agent-harness/ports/approver-directory';
import { hex } from './erasure-backfill-contract';

/** One org's records; ids are ObjectId hex so every adapter takes them. */
export interface ApproverOrgSeed {
  orgId: string;
  users?: Array<{ userId: string; admin?: boolean; deleted?: boolean }>;
  groups?: Array<{ groupId: string; members: string[]; deleted?: boolean }>;
}

export function describeApproverDirectoryContract(
  name: string,
  create: (orgs: ApproverOrgSeed[]) => Promise<ApproverDirectory>,
): void {
  describe(`ApproverDirectory contract: ${name}`, () => {
    it("AD-5: admins are the org's active users with the admin role, of that org only", async () => {
      const [org, other] = [hex(), hex()];
      const [admin, member, removed] = [hex(), hex(), hex()];
      const directory = await create([
        {
          orgId: org,
          users: [{ userId: admin, admin: true }, { userId: member }, { userId: removed, admin: true, deleted: true }],
        },
        { orgId: other },
      ]);
      expect(await directory.isOrgAdmin(org, admin)).to.equal(true);
      expect(await directory.isOrgAdmin(org, member)).to.equal(false);
      expect(await directory.isOrgAdmin(org, removed)).to.equal(false);
      expect(await directory.isOrgAdmin(other, admin)).to.equal(false);
      expect(await directory.isOrgAdmin(org, hex())).to.equal(false);
    });

    it('AD-6: nobody but the run\'s actor approves: a user, a group member and an admin are no approvers', async () => {
      const org = hex();
      const [admin, user, member, group] = [hex(), hex(), hex(), hex()];
      const directory = await create([
        {
          orgId: org,
          users: [{ userId: admin, admin: true }, { userId: user }, { userId: member }],
          groups: [{ groupId: group, members: [member] }],
        },
      ]);
      expect(await directory.isOrgAdmin(org, admin)).to.equal(true);
      for (const who of [admin, user, member]) {
        expect(await directory.isApprover(org, who), who).to.equal(false);
      }
    });

    it('AD-7: malformed ids are neither approvers nor admins', async () => {
      const org = hex();
      const [member, group] = [hex(), hex()];
      const directory = await create([
        {
          orgId: org,
          users: [{ userId: member, admin: true }],
          groups: [{ groupId: group, members: [member] }],
        },
      ]);
      expect(await directory.isApprover(org, 'not-a-user')).to.equal(false);
      expect(await directory.isApprover('not-an-org', member)).to.equal(false);
      expect(await directory.isOrgAdmin(org, 'not-a-user')).to.equal(false);
      expect(await directory.isOrgAdmin('not-an-org', member)).to.equal(false);
    });
  });
}
