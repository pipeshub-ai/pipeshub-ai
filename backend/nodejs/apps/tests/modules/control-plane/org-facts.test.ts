/** CPD-9 OF-1, OF-2 in memory (the Mongo users half runs in org-roles.mongo.itest.ts). */
import { ORG_A } from './support/cp-objects';
import { InMemoryDelegation, InMemoryOrgRoles } from './support/directory-world';
import { describeDelegationSourceContract, describeOrgRolesContract } from './support/org-facts-contract';

describeOrgRolesContract('InMemoryOrgRoles', async () => {
  const roles = new InMemoryOrgRoles();
  return { roles, setRole: async (orgId, userId, role) => roles.set(orgId, userId, role), user: 'u1', orgA: ORG_A, orgB: 'org-b' };
});

describeDelegationSourceContract('InMemoryDelegation', async () => {
  const delegation = new InMemoryDelegation();
  return {
    delegation,
    delegate: async (orgId, actions) => {
      if (actions === null) delegation.actions.delete(orgId);
      else delegation.actions.set(orgId, actions);
    },
    corrupt: async (orgId) => {
      delegation.actions.delete(orgId);
    },
  };
});

