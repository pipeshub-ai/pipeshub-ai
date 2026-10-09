import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { CommunityAdminLimitMigration } from '../../../../../src/modules/configuration_manager/services/migrations/community_admin_limit.migration';
import { editionMigrations } from '../../../../../src/modules/configuration_manager/services/migrations/edition.migrations';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { Users } from '../../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../../src/modules/user_management/schema/org.schema';
import { UserActivities } from '../../../../../src/modules/auth/schema/userActivities.schema';
import { NotificationContainer } from '../../../../../src/modules/notification/container/notification.container';
import { ADMIN_LIMIT_ENFORCEMENT_DATE } from '../../../../../src/modules/user_management/services/user-admin.service';
import { userActivitiesType } from '../../../../../src/libs/utils/userActivities.utils';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) =>
    Promise.resolve(path === configPaths.communityAdminLimitMigration ? existingFlag : null),
  ),
  set: sinon.stub().resolves(),
});

const beforeEnforcement = () => new Date(ADMIN_LIMIT_ENFORCEMENT_DATE.getTime() - 1);
const atEnforcement = () => new Date(ADMIN_LIMIT_ENFORCEMENT_DATE.getTime());

function stubAdmins(admins: Array<{ _id: mongoose.Types.ObjectId; email: string; createdAt: Date }>) {
  return sinon.stub(Users, 'find').returns({
    select: sinon.stub().returns({ lean: sinon.stub().resolves(admins) }),
  } as any);
}

function stubContactEmail(contactEmail: string | null) {
  return sinon.stub(Org, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().resolves(contactEmail ? { contactEmail } : null),
    }),
  } as any);
}

describe('CommunityAdminLimitMigration', () => {
  const orgId = new mongoose.Types.ObjectId();
  const owner = { _id: new mongoose.Types.ObjectId(), email: 'owner@a.test', createdAt: new Date('2025-03-01') };
  const early = { _id: new mongoose.Types.ObjectId(), email: 'early@a.test', createdAt: new Date('2025-01-01') };
  const late = { _id: new mongoose.Types.ObjectId(), email: 'late@a.test', createdAt: new Date('2026-01-01') };

  afterEach(() => {
    sinon.restore();
  });

  it('is registered as a Community Edition migration', () => {
    expect(editionMigrations).to.have.length(1);
  });

  it('does nothing before the enforcement date and leaves the flag unset', async () => {
    const kv = makeKvStore(null);
    const aggregate = sinon.stub(Users, 'aggregate');
    const updateMany = sinon.stub(Users, 'updateMany');

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      beforeEnforcement,
    ).run();

    expect(result.skipped).to.equal(true);
    expect(aggregate.called).to.equal(false);
    expect(updateMany.called).to.equal(false);
    expect(kv.get.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('skips when the completion flag is already set', async () => {
    const kv = makeKvStore('true');
    const aggregate = sinon.stub(Users, 'aggregate');

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result.skipped).to.equal(true);
    expect(aggregate.called).to.equal(false);
  });

  it('keeps the org contact as admin, makes the other admins members and signs them out', async () => {
    const kv = makeKvStore(null);
    const aggregate = sinon.stub(Users, 'aggregate').resolves([{ _id: orgId }] as any);
    stubAdmins([early, owner, late]);
    stubContactEmail('OWNER@a.test');
    const updateMany = sinon.stub(Users, 'updateMany').resolves({ modifiedCount: 2 } as any);
    const insertMany = sinon.stub(UserActivities, 'insertMany').resolves([] as any);
    const emitForceLogout = sinon.stub();
    sinon.stub(NotificationContainer, 'getNotificationService').returns({ emitForceLogout } as any);

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result).to.deep.equal({ skipped: false, orgsProcessed: 1, adminsDemoted: 2, errored: 0 });
    const pipeline = aggregate.firstCall.args[0] as any[];
    expect(pipeline[2]).to.deep.equal({ $match: { count: { $gt: 1 } } });

    const [filter, update] = updateMany.firstCall.args as any[];
    expect(filter._id.$in.map(String)).to.have.members([String(early._id), String(late._id)]);
    expect(filter).to.include({ orgId: String(orgId), role: 'admin' });
    expect(update).to.deep.equal({ $set: { role: 'member' } });

    const activities = insertMany.firstCall.args[0] as any[];
    expect(activities.map((a) => String(a.userId))).to.have.members([String(early._id), String(late._id)]);
    expect(activities.every((a) => a.activityType === userActivitiesType.ROLE_CHANGED)).to.equal(true);
    expect(insertMany.firstCall.calledBefore(updateMany.firstCall)).to.equal(true);
    expect(insertMany.secondCall.calledAfter(updateMany.firstCall)).to.equal(true);
    expect(emitForceLogout.callCount).to.equal(2);
    expect(kv.set.calledOnceWith(configPaths.communityAdminLimitMigration, 'true')).to.equal(true);
  });

  it('keeps the longest-standing admin when no admin is the org contact', async () => {
    const kv = makeKvStore(null);
    sinon.stub(Users, 'aggregate').resolves([{ _id: orgId }] as any);
    stubAdmins([owner, late, early]);
    stubContactEmail('someone-else@a.test');
    const updateMany = sinon.stub(Users, 'updateMany').resolves({} as any);
    sinon.stub(UserActivities, 'insertMany').resolves([] as any);
    sinon.stub(NotificationContainer, 'getNotificationService').returns(null);

    await new CommunityAdminLimitMigration(makeLogger() as any, kv as any, atEnforcement).run();

    const demotedIds = (updateMany.firstCall.args[0] as any)._id.$in.map(String);
    expect(demotedIds).to.have.members([String(owner._id), String(late._id)]);
    expect(demotedIds).to.not.include(String(early._id));
  });

  it('leaves the flag unset when an org fails, so the next boot retries', async () => {
    const kv = makeKvStore(null);
    sinon.stub(Users, 'aggregate').resolves([{ _id: orgId }] as any);
    stubAdmins([owner, late]);
    stubContactEmail('owner@a.test');
    sinon.stub(UserActivities, 'insertMany').resolves([] as any);
    sinon.stub(Users, 'updateMany').rejects(new Error('write failed'));

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result.errored).to.equal(1);
    expect(kv.set.called).to.equal(false);
  });

  it('does not demote anyone when their sessions cannot be ended, so the retry still ends them', async () => {
    const kv = makeKvStore(null);
    sinon.stub(Users, 'aggregate').resolves([{ _id: orgId }] as any);
    stubAdmins([owner, late]);
    stubContactEmail('owner@a.test');
    sinon.stub(UserActivities, 'insertMany').rejects(new Error('insert failed'));
    const updateMany = sinon.stub(Users, 'updateMany').resolves({} as any);

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result.errored).to.equal(1);
    expect(updateMany.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('still counts the org as done when only the second role-change record fails', async () => {
    const kv = makeKvStore(null);
    sinon.stub(Users, 'aggregate').resolves([{ _id: orgId }] as any);
    stubAdmins([owner, late]);
    stubContactEmail('owner@a.test');
    const insertMany = sinon.stub(UserActivities, 'insertMany');
    insertMany.onFirstCall().resolves([] as any);
    insertMany.onSecondCall().rejects(new Error('insert failed'));
    sinon.stub(Users, 'updateMany').resolves({} as any);
    sinon.stub(NotificationContainer, 'getNotificationService').returns(null);

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result).to.include({ adminsDemoted: 1, errored: 0 });
    expect(kv.set.calledOnce).to.equal(true);
  });

  it('completes without changes when no org is over the limit', async () => {
    const kv = makeKvStore(null);
    sinon.stub(Users, 'aggregate').resolves([] as any);
    const updateMany = sinon.stub(Users, 'updateMany');

    const result = await new CommunityAdminLimitMigration(
      makeLogger() as any,
      kv as any,
      atEnforcement,
    ).run();

    expect(result).to.deep.equal({ skipped: false, orgsProcessed: 0, adminsDemoted: 0, errored: 0 });
    expect(updateMany.called).to.equal(false);
    expect(kv.set.calledOnce).to.equal(true);
  });
});
