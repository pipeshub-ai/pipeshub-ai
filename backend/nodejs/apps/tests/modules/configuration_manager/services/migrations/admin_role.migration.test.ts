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
    updateManyStub.onCall(0).resolves({ modifiedCount: 1, matchedCount: 1 } as any); // promote
    updateManyStub.onCall(1).resolves({ modifiedCount: 2 } as any); // members in org
    updateManyStub.onCall(2).resolves({ modifiedCount: 0 } as any); // global default
    sinon.stub(Users, 'countDocuments').resolves(1);

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

  it('does not soft-delete admin group when promote leaves org with zero admins', async () => {
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
    updateManyStub.onCall(0).resolves({ modifiedCount: 0, matchedCount: 0 } as any);
    updateManyStub.onCall(1).resolves({ modifiedCount: 0 } as any); // global default only
    sinon.stub(Users, 'countDocuments').resolves(0);
    const updateOneStub = sinon.stub(UserGroups, 'updateOne');

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(1);
    expect(result.adminGroupsSoftDeleted).to.equal(0);
    expect(updateOneStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('skips when migration flag is already set', async () => {
    const kv = makeKvStore('true');
    const findStub = sinon.stub(UserGroups, 'find');

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result).to.deep.equal({
      adminGroupsProcessed: 0,
      usersPromoted: 0,
      usersDefaultedToMember: 0,
      adminGroupsSoftDeleted: 0,
      errored: 0,
    });
    expect(findStub.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('continues when reading the migration flag fails', async () => {
    const kv = {
      get: sinon.stub().rejects(new Error('kv unavailable')),
      set: sinon.stub().resolves(),
    };
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([]),
      }),
    } as any);
    sinon.stub(Users, 'updateMany').resolves({ modifiedCount: 0 } as any);

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(0);
    expect(kv.set.calledWith(configPaths.adminRoleMigration, 'true')).to.equal(
      true,
    );
  });

  it('skips promote when admin group has no users and ignores soft-delete no-op', async () => {
    const kv = makeKvStore(null);
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([
          {
            _id: groupId,
            orgId,
            users: [],
          },
        ]),
      }),
    } as any);

    const updateManyStub = sinon.stub(Users, 'updateMany');
    updateManyStub.onCall(0).resolves({ modifiedCount: 1 } as any); // org members
    updateManyStub.onCall(1).resolves({ modifiedCount: 0 } as any); // global default
    sinon.stub(UserGroups, 'updateOne').resolves({ modifiedCount: 0 } as any);

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(0);
    expect(result.adminGroupsProcessed).to.equal(1);
    expect(result.usersPromoted).to.equal(0);
    expect(result.usersDefaultedToMember).to.equal(1);
    expect(result.adminGroupsSoftDeleted).to.equal(0);
    expect(updateManyStub.callCount).to.equal(2);
  });

  it('records an error when listing admin groups fails', async () => {
    const kv = makeKvStore(null);
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().rejects(new Error('find failed')),
      }),
    } as any);

    const result = await new AdminRoleMigration(
      makeLogger() as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(1);
    expect(kv.set.called).to.equal(false);
  });

  it('warns when writing the completion flag fails after success', async () => {
    const logger = makeLogger();
    const kv = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().rejects(new Error('set failed')),
    };
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([]),
      }),
    } as any);
    sinon.stub(Users, 'updateMany').resolves({ modifiedCount: 0 } as any);

    const result = await new AdminRoleMigration(
      logger as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(0);
    expect(logger.warn.called).to.equal(true);
  });

  it('handles undefined users, missing modifiedCount, and non-Error throws', async () => {
    const logger = makeLogger();
    const kv = {
      get: sinon.stub().rejects('flag-read-failed'),
      set: sinon.stub().rejects('flag-write-failed'),
    };
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().resolves([
          {
            _id: groupId,
            orgId,
            users: undefined,
          },
          {
            _id: new mongoose.Types.ObjectId(),
            orgId,
            users: [adminUserId, null, undefined],
          },
        ]),
      }),
    } as any);

    const updateManyStub = sinon.stub(Users, 'updateMany');
    // first group: no promote (empty after filter), org members
    updateManyStub.onCall(0).resolves({} as any);
    // second group: promote + org members
    updateManyStub.onCall(1).resolves({ matchedCount: 1 } as any); // modifiedCount undefined → ?? 0
    updateManyStub.onCall(2).resolves({ modifiedCount: 1 } as any);
    // global default
    updateManyStub.onCall(3).resolves({ modifiedCount: 0 } as any);
    sinon.stub(Users, 'countDocuments').resolves(1);

    sinon.stub(UserGroups, 'updateOne').resolves({ modifiedCount: 1 } as any);

    const result = await new AdminRoleMigration(
      logger as any,
      kv as any,
    ).run();

    expect(result.errored).to.equal(0);
    expect(result.adminGroupsProcessed).to.equal(2);
    expect(result.usersPromoted).to.equal(0);
    expect(logger.warn.called).to.equal(true);
  });

  it('stringifies non-Error failures in per-group and outer catches', async () => {
    const logger = makeLogger();
    const kv = makeKvStore(null);

    // First run: per-group non-Error reject
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
    updateManyStub.onCall(0).rejects('promote-failed');
    updateManyStub.resolves({ modifiedCount: 0 } as any);
    sinon.stub(UserGroups, 'updateOne');

    const perGroup = await new AdminRoleMigration(
      logger as any,
      kv as any,
    ).run();
    expect(perGroup.errored).to.equal(1);
    expect(logger.error.called).to.equal(true);

    sinon.restore();

    // Second run: outer find failure with non-Error
    const logger2 = makeLogger();
    const kv2 = makeKvStore(null);
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().rejects('list-failed'),
      }),
    } as any);

    const outer = await new AdminRoleMigration(
      logger2 as any,
      kv2 as any,
    ).run();
    expect(outer.errored).to.equal(1);
    expect(logger2.error.called).to.equal(true);
  });
});
