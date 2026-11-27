/**
 * OF: what every `OrgRoles` and `DelegationSource` does (CPD-9): the live role
 * and the org's delegation the authorizer reads, per org, never another org's.
 */
import { expect } from 'chai';
import type { DelegationSource, OrgRole, OrgRoles } from '../../../../src/modules/control-plane/ports/org-roles';
import { ORG_A, ORG_B } from './cp-objects';

export interface OrgRolesWorld {
  readonly roles: OrgRoles;
  setRole(orgId: string, userId: string, role: OrgRole | null): Promise<void>;
  /** A user id the world's store can hold. */
  readonly user: string;
  readonly orgA: string;
  readonly orgB: string;
}

export function describeOrgRolesContract(name: string, make: () => Promise<OrgRolesWorld>): void {
  describe(`OrgRoles contract (OF-1): ${name}`, () => {
    it('OF-1: the live role in that org; a change is seen on the next read; nobody is null', async () => {
      const world = await make();
      expect(await world.roles.roleOf(world.orgA, world.user)).to.equal(null);
      await world.setRole(world.orgA, world.user, 'admin');
      expect(await world.roles.roleOf(world.orgA, world.user)).to.equal('admin');
      expect(await world.roles.roleOf(world.orgB, world.user)).to.equal(null);
      await world.setRole(world.orgA, world.user, 'member');
      expect(await world.roles.roleOf(world.orgA, world.user)).to.equal('member');
      await world.setRole(world.orgA, world.user, null);
      expect(await world.roles.roleOf(world.orgA, world.user)).to.equal(null);
    });
  });
}

export interface DelegationWorld {
  readonly delegation: DelegationSource;
  delegate(orgId: string, actions: readonly string[] | null): Promise<void>;
  /** Makes the org's stored policy unreadable. */
  corrupt(orgId: string): Promise<void>;
}

export function describeDelegationSourceContract(name: string, make: () => Promise<DelegationWorld>): void {
  describe(`DelegationSource contract (OF-2): ${name}`, () => {
    it('OF-2: nothing until the org names actions; the org\'s own set; never another org\'s', async () => {
      const world = await make();
      expect(await world.delegation.delegatedActions(ORG_A)).to.deep.equal([]);
      await world.delegate(ORG_A, ['grant.write', 'installation.create']);
      expect([...(await world.delegation.delegatedActions(ORG_A))].sort()).to.deep.equal(['grant.write', 'installation.create']);
      expect(await world.delegation.delegatedActions(ORG_B)).to.deep.equal([]);
    });

    it('OF-2: an unreadable policy delegates nothing (fail closed)', async () => {
      const world = await make();
      await world.delegate(ORG_A, ['grant.write']);
      await world.corrupt(ORG_A);
      expect(await world.delegation.delegatedActions(ORG_A)).to.deep.equal([]);
    });
  });
}
