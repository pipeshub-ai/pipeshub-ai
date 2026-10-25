/**
 * Save hooks against a real MongoDB. Mongoose 9 dropped the `next` callback
 * from pre middleware, so a hook written for the callback style fails every
 * save with "next is not a function" -- something only a real save shows.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { UserGroups } from '../../src/modules/user_management/schema/userGroup.schema';
import { Users } from '../../src/modules/user_management/schema/users.schema';
import { OAuthApp } from '../../src/modules/oauth_provider/schema/oauth.app.schema';
import { CrawlingManagerConfig } from '../../src/modules/crawling_manager/schema/schema';
import { UserCredentials } from '../../src/modules/auth/schema/userCredentials.schema';
import { userActivitiesType } from '../../src/libs/utils/userActivities.utils';
import { UserActivities } from '../../src/modules/auth/schema/userActivities.schema';

describe('Save hooks against a real MongoDB', function () {
  this.timeout(30000);
  const uri = process.env.MONGO_IT_URI;
  const orgId = new mongoose.Types.ObjectId();

  before(async function () {
    if (!uri) this.skip();
    await mongoose.connect(uri, { dbName: `pipeshub_it_hooks_${Date.now()}`, serverSelectionTimeoutMS: 5000 });
  });
  after(async () => {
    if (!uri) return;
    await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });

  it('gives a new org a slug', async () => {
    const org = await new Org({ domain: 'it.example', contactEmail: 'o@it.example', accountType: 'individual' }).save();
    expect(org.slug).to.be.a('string').and.not.empty;
  });

  it('gives a new group a slug', async () => {
    const group = await new UserGroups({ name: 'it-group', type: 'custom', orgId }).save();
    expect(group.slug).to.be.a('string').and.not.empty;
  });

  it('gives a new user a slug', async () => {
    const user = await new Users({ orgId, email: 'u@it.example', fullName: 'It User' }).save();
    expect(user.slug).to.be.a('string').and.not.empty;
  });

  it('still refuses a service account with the admin role (the hook error reaches the caller)', async () => {
    let message = '';
    try {
      await new Users({ orgId, email: 'sa@it.example', fullName: 'Svc', kind: 'service', role: 'admin' }).save();
    } catch (error) {
      message = (error as Error).message;
    }
    expect(message).to.include('service account');
  });

  it('gives a new OAuth app a slug', async () => {
    const app = await new OAuthApp({
      clientId: 'it-client',
      clientSecretEncrypted: 'x',
      name: 'It app',
      orgId,
      createdBy: new mongoose.Types.ObjectId(),
      allowedScopes: ['a'],
    }).save();
    expect(app.slug).to.be.a('string').and.not.empty;
  });

  it('stamps lastStatusUpdate when a crawling config changes status', async () => {
    const userId = new mongoose.Types.ObjectId();
    const config = await new CrawlingManagerConfig({
      orgId,
      configName: 'it-config',
      createdBy: userId,
      lastUpdatedBy: userId,
      crawlingSchedule: {
        scheduleType: 'hourly',
        isEnabled: true,
        createdBy: userId,
        lastUpdatedBy: userId,
        scheduleConfig: { scheduleType: 'hourly', minute: 5, timezone: 'UTC' },
      },
    }).save();
    const before = (await CrawlingManagerConfig.findById(config._id).lean())!.lastStatusUpdate as Date;
    await new Promise((resolve) => setTimeout(resolve, 30));
    config.currentStatus = 'running' as never;
    await config.save();
    const after = (await CrawlingManagerConfig.findById(config._id).lean())!.lastStatusUpdate as Date;
    expect(after.getTime()).to.be.greaterThan(before.getTime());
  });

  it('runs validators on findOneAndUpdate for credentials and user activity', async () => {
    const credential = await UserCredentials.create({ userId: String(orgId), orgId: String(orgId), ipAddress: '1.1.1.1' });
    // `required` is only enforced on an update when the hook turns runValidators on.
    let credentialError = '';
    try {
      await UserCredentials.findOneAndUpdate({ _id: credential._id }, { $set: { orgId: null } });
    } catch (error) {
      credentialError = (error as Error).name;
    }
    expect(credentialError).to.equal('ValidationError');

    const activity = await UserActivities.create({
      userId: orgId,
      orgId,
      activityType: userActivitiesType.LOGIN,
      ipAddress: '1.1.1.1',
    } as never);
    let activityError = '';
    try {
      await UserActivities.findOneAndUpdate({ _id: activity._id }, { activityType: 'not-an-activity' });
    } catch (error) {
      activityError = (error as Error).name;
    }
    expect(activityError).to.equal('ValidationError');
  });

  it('applies the service-account rules to updates as well as saves', async () => {
    const account = await new Users({ orgId, email: 'sa-upd@service.pipeshub.internal', fullName: 'Svc', kind: 'service', role: 'member' }).save();
    const refused = async (work: PromiseLike<unknown>) => {
      let message = '';
      try {
        await work;
      } catch (error) {
        message = (error as Error).message;
      }
      expect(message).to.match(/service account/i);
    };
    await refused(Users.findOneAndUpdate({ _id: account._id }, { role: 'admin' }));
    await refused(Users.updateOne({ _id: account._id }, { $set: { role: 'admin' } }));
    await refused(Users.updateMany({ _id: account._id }, { role: 'admin' }));
    const person = await new Users({ orgId, email: 'person-upd@it.example', fullName: 'Person' }).save();
    await refused(Users.findOneAndUpdate({ _id: person._id }, { email: 'person-upd@service.pipeshub.internal' }));
    const renamed = await Users.findOneAndUpdate({ _id: person._id }, { fullName: 'Renamed' }, { returnDocument: 'after' });
    expect(renamed!.fullName).to.equal('Renamed');
  });
});
