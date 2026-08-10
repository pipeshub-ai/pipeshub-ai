import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { AdminRoleMigration } from '../../../../../src/modules/configuration_manager/services/migrations/admin_role.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { UserGroups } from '../../../../../src/modules/user_management/schema/userGroup.schema';
import { Users } from '../../../../../src/modules/user_management/schema/users.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) => {
    if (path === configPaths.adminRoleMigration) {
      return Promise.resolve(existingFlag);
    }
    return Promise.resolve(null);
  }),
  set: sinon.stub().resolves(),
});

describe('AdminRoleMigration', () => {
  const orgId = new mongoose.Types.ObjectId();
  const adminUserId = new mongoose.Types.ObjectId();
  const groupId = new mongoose.Types.ObjectId();

  afterEach(() => {
    sinon.restore();
  });

  it('promotes admin-group members, defaults others to member, soft-deletes group', async () => {
    const kv = makeKvStore(null);
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([
          {
            _id: groupId,
            orgId,
            users: [adminUserId],
          },
        ]),
      }),
    } as any);

    const updateManyStub = sinon.stub(Users, 'updateMany');
    updateManyStub.onCall(0).resolves({ modifiedCount: 1 } as any); // promote
    updateManyStub.onCall(1).resolves({ modifiedCount: 2 } as any); // members in org
    updateManyStub.onCall(2).resolves({ modifiedCount: 0 } as any); // global default

    const updateOneStub = sinon.stub(UserGroups, 'updateOne').resolves({
      modifiedCount: 1,
    } as any);

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(0);
    expect(result.adminGroupsProcessed).to.equal(1);
    expect(result.usersPromoted).to.equal(1);
    expect(result.usersDefaultedToMember).to.equal(2);
    expect(result.adminGroupsSoftDeleted).to.equal(1);

    expect(updateManyStub.firstCall.args[0]).to.deep.include({
      orgId,
    });
    expect(updateManyStub.firstCall.args[1]).to.deep.equal({
      $set: { role: 'admin' },
    });
    expect(updateManyStub.secondCall.args[0]).to.deep.equal({
      orgId,
      isDeleted: { $ne: true },
      $or: [{ role: { $exists: false } }, { role: null }],
    });

    expect(updateOneStub.calledOnce).to.equal(true);
    expect(updateOneStub.firstCall.args[0]).to.deep.equal({ _id: groupId });
    expect(updateOneStub.firstCall.args[1]).to.deep.equal({
      $set: { isDeleted: true },
    });

    expect(kv.set.calledWith(configPaths.adminRoleMigration, 'true')).to.equal(
      true,
    );
  });

  it('does not write completion flag when a per-group error occurs', async () => {
    const kv = makeKvStore(null);
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([
          {
            _id: groupId,
            orgId,
            users: [adminUserId],
          },
        ]),
      }),
    } as any);

    const updateManyStub = sinon.stub(Users, 'updateMany');
    updateManyStub.onCall(0).rejects(new Error('db down'));
    updateManyStub.resolves({ modifiedCount: 0 } as any);
    sinon.stub(UserGroups, 'updateOne');

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(1);
    expect(result.adminGroupsProcessed).to.equal(1);
    expect(kv.set.called).to.equal(false);
  });
});
