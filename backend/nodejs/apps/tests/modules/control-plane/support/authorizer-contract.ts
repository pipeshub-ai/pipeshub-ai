/**
 * AZ: what every `Authorizer` does (03-interfaces §5.1); CPD-9 runs it on
 * `RoleAuthorizer` and adds the shared matrix vectors
 * (`contracts/control-plane/vectors/authz.json`, team delegation included).
 */
import { expect } from 'chai';
import type { Authorizer, CpAction } from '../../../../src/modules/control-plane/ports/authorizer';
import { ORG_A, ORG_B } from './cp-objects';

export interface AuthorizerWorld {
  readonly authorizer: Authorizer;
  /** Makes the user an org admin, a member, or nobody in the org; the next check must see it. */
  setRole(orgId: string, userId: string, role: 'admin' | 'member' | null): Promise<void>;
}

const READS: CpAction[] = ['catalog.read', 'installation.read', 'grant.read', 'binding.read', 'approval.read'];
const WRITES: CpAction[] = [
  'catalog.import',
  'installation.create',
  'installation.update',
  'installation.delete',
  'grant.write',
  'policy.write:approvals',
  'binding.create',
  'binding.update',
  'approval.decide',
  'audit.read',
];

export function describeAuthorizerContract(name: string, make: () => Promise<AuthorizerWorld>): void {
  describe(`Authorizer contract (AZ): ${name}`, () => {
    let world: AuthorizerWorld;
    const admin = { orgId: ORG_A, userId: 'u-admin' };
    const member = { orgId: ORG_A, userId: 'u-member' };
    const orgResource = { orgId: ORG_A, type: 'installation', owner: 'org' };

    beforeEach(async () => {
      world = await make();
      await world.setRole(ORG_A, admin.userId, 'admin');
      await world.setRole(ORG_A, member.userId, 'member');
    });

    it('AZ-1: an org admin may do everything in the org; a member reads, and is denied writes with a reason', async () => {
      for (const action of [...READS, ...WRITES]) {
        expect(await world.authorizer.check(admin, action, orgResource), action).to.deep.equal({ allow: true });
      }
      for (const action of READS) {
        expect(await world.authorizer.check(member, action, orgResource), action).to.deep.equal({ allow: true });
      }
      for (const action of WRITES) {
        const decision = await world.authorizer.check(member, action, orgResource);
        expect(decision.allow, action).to.equal(false);
        expect((decision as { reason: string }).reason, action).to.be.a('string').and.not.equal('');
      }
    });

    it('AZ-1: a member may connect their own account but not another user\'s', async () => {
      const own = { orgId: ORG_A, type: 'binding', owner: `user:${member.userId}` };
      const other = { orgId: ORG_A, type: 'binding', owner: 'user:u-other' };
      expect(await world.authorizer.check(member, 'binding.create', own)).to.deep.equal({ allow: true });
      expect((await world.authorizer.check(member, 'binding.create', other)).allow).to.equal(false);
    });

    it('AZ-2: a resource in another org is denied whatever the role', async () => {
      for (const action of [...READS, ...WRITES]) {
        const decision = await world.authorizer.check(admin, action, { ...orgResource, orgId: ORG_B });
        expect(decision.allow, action).to.equal(false);
      }
    });

    it('AZ-3: a demoted admin is denied on the next check', async () => {
      expect(await world.authorizer.check(admin, 'grant.write', orgResource)).to.deep.equal({ allow: true });
      await world.setRole(ORG_A, admin.userId, 'member');
      expect((await world.authorizer.check(admin, 'grant.write', orgResource)).allow).to.equal(false);
      await world.setRole(ORG_A, admin.userId, null);
      expect((await world.authorizer.check(admin, 'catalog.read', orgResource)).allow).to.equal(false);
    });
  });
}
