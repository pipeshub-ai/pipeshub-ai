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
});
