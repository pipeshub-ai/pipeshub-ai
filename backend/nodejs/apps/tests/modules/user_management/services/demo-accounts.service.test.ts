import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import {
  clearRemovedSampleAccount,
  isDemoAccountEmail,
  setSampleAccountsSignIn,
} from '../../../../src/modules/user_management/services/demo-accounts.service';

describe('sample accounts', () => {
  afterEach(() => sinon.restore());

  it('recognises only the reserved demo domain', () => {
    expect(isDemoAccountEmail('Bob@Acme-Demo.example')).to.equal(true);
    expect(isDemoAccountEmail('bob@acme-demo.example.com')).to.equal(false);
    expect(isDemoAccountEmail(undefined)).to.equal(false);
  });

  it('switches sign-in for the org sample accounts, never the caller', async () => {
    const update = sinon
      .stub(Users, 'updateMany')
      .resolves({ modifiedCount: 2 } as any);
    const caller = '64b7f0c2a1b2c3d4e5f60718';

    expect(await setSampleAccountsSignIn('org-1', caller, false)).to.equal(2);

    const [filter, change] = update.firstCall.args as any[];
    expect(filter.orgId).to.equal('org-1');
    expect(filter.isDeleted).to.equal(false);
    expect((filter.email as RegExp).test('alice@acme-demo.example')).to.equal(
      true,
    );
    expect((filter.email as RegExp).test('alice@acme-demo.examplex')).to.equal(
      false,
    );
    expect(String(filter._id.$ne)).to.equal(caller);
    expect(change).to.deep.equal({ $set: { isDisabled: true } });
  });

  it("clears this org's removed sample account so its address can be used again", async () => {
    const find = sinon
      .stub(Users, 'find')
      .returns({
        select: () => ({ lean: async () => [{ _id: 'old-1' }] }),
      } as any);
    const remove = sinon
      .stub(Users, 'findOneAndDelete')
      .returns({
        select: () => ({ lean: async () => ({ _id: 'old-1' }) }),
      } as any);
    const creds = sinon.stub(UserCredentials, 'deleteMany').resolves({} as any);

    await clearRemovedSampleAccount('bob@acme-demo.example', 'org-1');

    // Another org's removed account with the same address is not touched.
    expect(find.firstCall.args[0]).to.deep.equal({
      email: 'bob@acme-demo.example',
      orgId: 'org-1',
      isDeleted: true,
    });
    expect(remove.firstCall.args[0]).to.deep.equal({
      _id: 'old-1',
      orgId: 'org-1',
      isDeleted: true,
    });
    expect(creds.firstCall.args[0]).to.deep.equal({ userId: 'old-1' });
    expect(creds.calledAfter(remove)).to.equal(true);
  });

  it('keeps the credentials of an account an invite restored meanwhile', async () => {
    sinon
      .stub(Users, 'find')
      .returns({
        select: () => ({ lean: async () => [{ _id: 'old-1' }] }),
      } as any);
    sinon
      .stub(Users, 'findOneAndDelete')
      .returns({ select: () => ({ lean: async () => null }) } as any);
    const creds = sinon.stub(UserCredentials, 'deleteMany');

    await clearRemovedSampleAccount('bob@acme-demo.example', 'org-1');

    expect(creds.called).to.equal(false);
  });

  it('does nothing when there is no removed account', async () => {
    sinon
      .stub(Users, 'find')
      .returns({ select: () => ({ lean: async () => [] }) } as any);
    const remove = sinon.stub(Users, 'findOneAndDelete');
    await clearRemovedSampleAccount('bob@acme-demo.example', 'org-1');
    expect(remove.called).to.equal(false);
  });
});
