import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';

function makeService() {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  const tokens = { revokeAllTokensForApp: sinon.stub().resolves() };
  const service = new OAuthAppService(
    logger as any,
    { encrypt: sinon.stub().returns('enc'), decrypt: sinon.stub() } as any,
    {
      validateRequestedScopes: sinon.stub(),
      getAllowedScopeNamesForRole: sinon.stub().returns([]),
    } as any,
    tokens as any,
  );
  return { service, tokens };
}

describe('updating the scopes of an OAuth app', () => {
  const appId = new Types.ObjectId().toString();
  const orgId = new Types.ObjectId().toString();
  const userId = new Types.ObjectId().toString();

  let app: any;

  beforeEach(() => {
    app = {
      _id: new Types.ObjectId(appId),
      clientId: 'client123',
      name: 'MCP app',
      redirectUris: [],
      allowedGrantTypes: [],
      allowedScopes: ['conversation:chat', 'kb:read'],
      save: sinon.stub().resolvesThis(),
    };
    sinon.stub(OAuthApp, 'findOne').resolves(app);
  });

  afterEach(() => sinon.restore());

  it('revokes every token of the app when a scope is removed', async () => {
    const { service, tokens } = makeService();

    await service.updateApp(appId, orgId, userId, true, { allowedScopes: ['kb:read'] });

    expect(app.allowedScopes).to.deep.equal(['kb:read']);
    expect(tokens.revokeAllTokensForApp.calledOnceWith('client123')).to.equal(true);
  });

  it('revokes nothing when scopes are only added', async () => {
    const { service, tokens } = makeService();

    await service.updateApp(appId, orgId, userId, true, {
      allowedScopes: ['conversation:chat', 'kb:read', 'agent:read'],
    });

    expect(tokens.revokeAllTokensForApp.called).to.equal(false);
  });

  it('revokes nothing when the scopes are not part of the update', async () => {
    const { service, tokens } = makeService();

    await service.updateApp(appId, orgId, userId, true, { name: 'Renamed' });

    expect(tokens.revokeAllTokensForApp.called).to.equal(false);
  });

  it('puts the scopes back and fails when the revocation fails', async () => {
    // Otherwise the app would advertise the smaller set while every token
    // already issued kept the removed scope.
    const { service, tokens } = makeService();
    tokens.revokeAllTokensForApp.rejects(new Error('mongo down'));

    try {
      await service.updateApp(appId, orgId, userId, true, { allowedScopes: ['kb:read'] });
      expect.fail('should have thrown');
    } catch (error) {
      expect((error as Error).message).to.equal('mongo down');
    }

    expect(app.allowedScopes).to.deep.equal(['conversation:chat', 'kb:read']);
    expect(app.save.calledTwice).to.equal(true);
  });
});
