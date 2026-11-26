/** OF-1, OF-2 on a MongoDB replica set: `LiveOrgRoles` over the users collection (the memory half runs in org-facts.test.ts). */
import 'reflect-metadata';
import { Model, Types } from 'mongoose';
import { LiveOrgRoles } from '../../../src/libs/repositories/control-plane/user-directory/mongo';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { describeOrgRolesContract } from './support/org-facts-contract';

registerHarnessModels([Users] as unknown as Array<Model<never>>);

describe('control-plane org roles on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeOrgRolesContract('LiveOrgRoles over the users collection', async () => {
    const user = new Types.ObjectId().toString();
    const orgA = new Types.ObjectId().toString();
    return {
      roles: new LiveOrgRoles(),
      user,
      orgA,
      orgB: new Types.ObjectId().toString(),
      setRole: async (orgId, userId, role) => {
        await Users.deleteMany({ _id: userId }).exec();
        if (role !== null) {
          await Users.create({ _id: new Types.ObjectId(userId), orgId: new Types.ObjectId(orgId), email: `${userId}@example.com`, role });
        }
      },
    };
  });
});
