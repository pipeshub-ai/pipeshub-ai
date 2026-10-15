import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { findSignInRemovalNotice } from '../../../../src/modules/auth/services/signInRemoval.service';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

const chain = (value: unknown) => {
  const select = sinon.stub().returns({ lean: sinon.stub().resolves(value) });
  return { select };
};

describe('findSignInRemovalNotice', () => {
  const orgId = new mongoose.Types.ObjectId().toString();

  afterEach(() => {
    sinon.restore();
  });

  it('returns nothing to show when the workspace has no policy', async () => {
    sinon.stub(OrgAuthConfig, 'findOne').returns(chain(null) as any);
    const usersFind = sinon.stub(Users, 'find');

    const notice = await findSignInRemovalNotice(orgId);

    expect(notice).to.deep.equal({ removedMethods: [], peopleWithoutPassword: [] });
    expect(usersFind.called).to.equal(false);
  });

  it('returns nothing to show when the upgrade removed nothing', async () => {
    const findOne = sinon
      .stub(OrgAuthConfig, 'findOne')
      .returns(chain({ removedSignInMethods: [] }) as any);
    const usersFind = sinon.stub(Users, 'find');

    const notice = await findSignInRemovalNotice(orgId);

    expect(notice).to.deep.equal({ removedMethods: [], peopleWithoutPassword: [] });
    expect(usersFind.called).to.equal(false);
    expect(findOne.firstCall.args[0]).to.deep.equal({ orgId, isDeleted: false });
  });

  it('lists people who have signed in without ever setting a password', async () => {
    const withPassword = new mongoose.Types.ObjectId();
    const named = new mongoose.Types.ObjectId();
    const unnamed = new mongoose.Types.ObjectId();
    sinon
      .stub(OrgAuthConfig, 'findOne')
      .returns(chain({ removedSignInMethods: ['samlSso', 'oauth'] }) as any);
    const usersFind = sinon.stub(Users, 'find').returns(
      chain([
        { _id: withPassword, fullName: 'Has Password', email: 'has@acme.example' },
        { _id: named, fullName: 'Alice Chen', email: 'alice@acme.example' },
        { _id: unnamed, email: 'bob@acme.example' },
      ]) as any,
    );
    const credsFind = sinon
      .stub(UserCredentials, 'find')
      .returns(chain([{ userId: String(withPassword) }]) as any);

    const notice = await findSignInRemovalNotice(orgId);

    expect(notice).to.deep.equal({
      removedMethods: ['samlSso', 'oauth'],
      peopleWithoutPassword: [
        { userId: String(named), fullName: 'Alice Chen', email: 'alice@acme.example' },
        { userId: String(unnamed), email: 'bob@acme.example' },
      ],
    });
    expect((usersFind.firstCall.args as unknown[])[0]).to.deep.equal({
      orgId,
      isDeleted: { $ne: true },
      hasLoggedIn: true,
      isDisabled: { $ne: true },
      kind: { $ne: 'service' },
    });
    expect((credsFind.firstCall.args as unknown[])[0]).to.deep.equal({
      orgId,
      isDeleted: { $ne: true },
      userId: { $in: [String(withPassword), String(named), String(unnamed)] },
      hashedPassword: { $exists: true, $nin: [null, ''] },
    });
  });
});
