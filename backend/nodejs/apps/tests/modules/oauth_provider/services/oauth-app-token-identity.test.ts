import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

function makeService() {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  return new OAuthAppService(
    logger as any,
    { encrypt: sinon.stub().returns('enc'), decrypt: sinon.stub() } as any,
    { getAllowedScopeNamesForRole: sinon.stub().returns([]) } as any,
  );
}

function stubUser(doc: Record<string, unknown> | null) {
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().returns({ exec: sinon.stub().resolves(doc) }),
    }),
  } as any);
}

describe('pointing an OAuth app at a service account', () => {
  const appId = new Types.ObjectId().toString();
  const orgId = new Types.ObjectId().toString();
  const userId = new Types.ObjectId().toString();
  const serviceAccountId = new Types.ObjectId().toString();

  let app: any;

  beforeEach(() => {
    app = {
      _id: appId,
      createdBy: new Types.ObjectId(userId),
      tokenIdentityUserId: undefined,
      clientId: 'client123',
      name: 'Nightly sync app',
      redirectUris: [],
      allowedGrantTypes: [],
      allowedScopes: [],
      save: sinon.stub().resolvesThis(),
    };
    sinon.stub(OAuthApp, 'findOne').resolves(app);
  });

  afterEach(() => sinon.restore());

  it('points the app at a service account', async () => {
    stubUser({ isDisabled: false });

    await makeService().setTokenIdentity(appId, orgId, userId, serviceAccountId);

    expect(app.tokenIdentityUserId.toString()).to.equal(serviceAccountId);
    expect(app.save.calledOnce).to.equal(true);
  });

  it('leaves createdBy alone, so the app stays manageable', async () => {
    // Moving createdBy would hide the app from Developer Settings for
    // everyone, because nobody can sign in as a service account.
    stubUser({ isDisabled: false });

    await makeService().setTokenIdentity(appId, orgId, userId, serviceAccountId);

    expect(app.createdBy.toString()).to.equal(userId);
  });

  it('puts the app back to acting as its creator when passed null', async () => {
    app.tokenIdentityUserId = new Types.ObjectId(serviceAccountId);

    await makeService().setTokenIdentity(appId, orgId, userId, null);

    expect(app.tokenIdentityUserId).to.equal(undefined);
  });

  it('refuses a colleague, not just any user id', async () => {
    // kind is part of the query, so a person's id finds nothing here and an
    // app cannot be made to act as them.
    const findOne = stubUser(null);

    try {
      await makeService().setTokenIdentity(appId, orgId, userId, serviceAccountId);
      expect.fail('expected a non-service account to be refused');
    } catch (error) {
      expect((error as Error).message).to.equal('Service account not found');
    }
    expect(findOne.firstCall.args[0]).to.include({ kind: 'service' });
  });

  it('refuses a disabled service account', async () => {
    stubUser({ isDisabled: true });

    try {
      await makeService().setTokenIdentity(appId, orgId, userId, serviceAccountId);
      expect.fail('expected a disabled service account to be refused');
    } catch (error) {
      expect((error as Error).message).to.contain('disabled');
    }
  });
});
