/** WP-73's store ports on their in-memory adapters (C-2): the replica set runs the same suites in wp73-store-contracts.mongo.itest.ts. */
import { InMemoryGroupMembership } from '../../../src/modules/agent-harness/adapters/in-memory-group-membership';
import { describeActorGroupMembershipContract } from './support/wp73-stores-contract';

describeActorGroupMembershipContract('in memory', async () => {
  const membership = new InMemoryGroupMembership();
  return {
    membership,
    group: async (orgId, groupId, users) => {
      for (const user of users) membership.add(orgId, groupId, user);
    },
    removeFrom: async (orgId, groupId, userId) => membership.remove(orgId, groupId, userId),
  };
});
