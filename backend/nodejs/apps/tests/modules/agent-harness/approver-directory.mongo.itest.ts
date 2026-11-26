/**
 * `HarnessApproverDirectory` over real users and user groups on a MongoDB
 * replica set, created through the user-management models.
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" REPLICA_SET_AVAILABLE=true \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 60000 tests/modules/agent-harness/approver-directory.mongo.itest.ts
 */
import 'reflect-metadata';
import { Model, Types } from 'mongoose';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  ApproverOrgSeed,
  describeApproverDirectoryContract,
} from './support/approver-directory-contract';
import { HarnessApproverDirectory } from '../../../src/libs/repositories/agent-harness/approver-directory/mongo';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';

registerHarnessModels([Users, UserGroups] as unknown as Array<Model<never>>);

async function directoryOver(orgs: ApproverOrgSeed[]): Promise<HarnessApproverDirectory> {
  await resetHarnessMongo();
  for (const org of orgs) {
    for (const user of org.users ?? []) {
      await Users.create({
        _id: new Types.ObjectId(user.userId),
        orgId: new Types.ObjectId(org.orgId),
        email: `${user.userId}@example.com`,
        role: user.admin ? 'admin' : 'member',
        isDeleted: user.deleted === true,
      });
    }
    for (const group of org.groups ?? []) {
      await UserGroups.create({
        _id: new Types.ObjectId(group.groupId),
        orgId: new Types.ObjectId(org.orgId),
        name: `group-${group.groupId}`,
        type: 'custom',
        users: group.members.map((id) => new Types.ObjectId(id)),
        isDeleted: group.deleted === true,
      });
    }
  }
  return new HarnessApproverDirectory();
}

describe('Approver directory on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeApproverDirectoryContract('HarnessApproverDirectory', directoryOver);
});
