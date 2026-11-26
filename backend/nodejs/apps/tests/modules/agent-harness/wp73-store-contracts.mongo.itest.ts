/**
 * WP-73's store ports on a MongoDB replica set (C-2): the actor's membership of
 * `UserGroups`, held to the same suites as their in-memory
 * adapters (wp73-store-contracts.test.ts).
 */
import 'reflect-metadata';
import { Model, Types } from 'mongoose';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { HarnessGroupMembership } from '../../../src/libs/repositories/agent-harness/group-membership/mongo';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { describeActorGroupMembershipContract } from './support/wp73-stores-contract';

registerHarnessModels([AgentRunModel, UserGroups] as unknown as Array<Model<never>>);

describe('WP-73 store contracts on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeActorGroupMembershipContract('Mongo', async () => ({
    membership: new HarnessGroupMembership(),
    group: async (orgId, groupId, users) => {
      await UserGroups.create({
        _id: new Types.ObjectId(groupId),
        orgId: new Types.ObjectId(orgId),
        name: `group-${groupId}`,
        type: 'custom',
        users: users.map((user) => new Types.ObjectId(user)),
      });
    },
    removeFrom: async (_orgId, groupId, userId) => {
      await UserGroups.updateOne({ _id: groupId }, { $pull: { users: new Types.ObjectId(userId) } });
    },
  }));
});
