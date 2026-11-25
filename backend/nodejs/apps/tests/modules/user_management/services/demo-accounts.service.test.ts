import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import {
  DEMO_ACCOUNT_DOMAIN,
  clearRemovedSampleAccount,
  isDemoAccountEmail,
  setSampleAccountsSignIn,
} from '../../../../src/modules/user_management/services/demo-accounts.service';
import { setUserRepository } from '../../../../src/libs/repositories/user';

describe('sample accounts', () => {
  afterEach(() => {
    sinon.restore();
    setUserRepository();
  });

  it('recognises only the reserved demo domain', () => {
    expect(isDemoAccountEmail('Bob@Acme-Demo.example')).to.equal(true);
    expect(isDemoAccountEmail('bob@acme-demo.example.com')).to.equal(false);
    expect(isDemoAccountEmail(undefined)).to.equal(false);
  });

  it('switches sign-in for the org sample accounts, never the caller', async () => {
    const setDisabled = sinon.stub().resolves(2);
    setUserRepository({ listAllLive: async () => [], setDisabledByEmailDomain: setDisabled } as never);
    const caller = '64b7f0c2a1b2c3d4e5f60718';

    expect(await setSampleAccountsSignIn('org-1', caller, false)).to.equal(2);

    // The org, the reserved demo domain only, never the caller, switched off.
    expect(setDisabled.firstCall.args).to.deep.equal(['org-1', DEMO_ACCOUNT_DOMAIN, caller, true]);
  });

  it('DEACT-6 (SEC-AZ-08): switching them off revokes their tokens and stops their harness work; switching on resumes nothing', async () => {
    const caller = '64b7f0c2a1b2c3d4e5f60718';
    const listAllLive = sinon.stub().resolves([
      { _id: 'alice', email: 'alice@acme-demo.example' },
      { _id: 'bob', email: 'bob@acme-demo.example' },
      // Only accounts still on are stopped, so a repeat stops nobody twice; never the caller; never a person.
      { _id: 'carol', email: 'carol@acme-demo.example', isDisabled: true },
      { _id: caller, email: 'me@acme-demo.example' },
      { _id: 'dave', email: 'dave@acme.example' },
    ]);
    setUserRepository({ listAllLive, setDisabledByEmailDomain: sinon.stub().resolves(2) } as never);
    const tokens = { revokeEveryTokenForUser: sinon.stub().resolves() };
    const deactivation = { deactivate: sinon.stub().resolves({ cancelledRuns: 1, pausedSchedules: 1 }) };

    await setSampleAccountsSignIn('org-1', caller, false, { tokens, deactivation });

    expect(listAllLive.firstCall.args).to.deep.equal(['org-1']);
    expect(tokens.revokeEveryTokenForUser.args.map((a) => a[0])).to.deep.equal(['alice', 'bob']);
    expect(deactivation.deactivate.args).to.deep.equal([
      ['org-1', 'alice', caller],
      ['org-1', 'bob', caller],
    ]);

    tokens.revokeEveryTokenForUser.resetHistory();
    deactivation.deactivate.resetHistory();
    await setSampleAccountsSignIn('org-1', caller, true, { tokens, deactivation });
    expect(tokens.revokeEveryTokenForUser.called).to.equal(false);
    expect(deactivation.deactivate.called).to.equal(false);
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
