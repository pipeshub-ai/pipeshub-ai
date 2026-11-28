import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service';
import { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import {
  RETIRED_SCOPE_MESSAGE,
  getAllowedScopeNamesForRole,
  withoutRetiredScopes,
} from '../../../../src/modules/oauth_provider/config/scopes.config';

// Organization configuration changes only from an interactive session, so no token may
// hold config:write: it is not offered, a request for it is refused with a clear 400,
// an app saved with it still loads and saves (the scope is dropped), and an authorization
// request that names it simply does not get it.

const scopes = new ScopeValidatorService();

const refusal = (run: () => unknown) => {
  try {
    run();
  } catch (error) {
    return error as { statusCode?: number; message?: string };
  }
  return null;
};

describe('the retired config:write scope', () => {
  afterEach(() => sinon.restore());

  it('is not offered to anyone, admins included', () => {
    expect(getAllowedScopeNamesForRole(true)).to.not.include('config:write');
    expect(getAllowedScopeNamesForRole(false)).to.not.include('config:write');
    expect(scopes.getAllScopes().map((s) => s.name)).to.not.include(
      'config:write',
    );
    expect(getAllowedScopeNamesForRole(true)).to.include('config:read');
  });

  it('a request for it (OAuth app, PAT, service token) gets a 400 that says why', () => {
    for (const run of [
      () =>
        scopes.validateRequestedScopes(
          ['kb:read', 'config:write'],
          getAllowedScopeNamesForRole(true),
        ),
      () =>
        scopes.validateScopesForApp(
          ['config:write'],
          ['config:write', 'kb:read'],
        ),
    ]) {
      const error = refusal(run);
      expect(error?.statusCode).to.equal(400);
      expect(error?.message).to.equal(RETIRED_SCOPE_MESSAGE);
    }
  });

  it('an authorization request that names it is granted the rest, not refused', () => {
    const { granted } = scopes.resolveGrantedScopes(
      ['config:write', 'kb:read'],
      ['config:write', 'kb:read'],
    );
    expect(granted).to.deep.equal(['kb:read']);
  });

  it('withoutRetiredScopes keeps every other scope', () => {
    expect(
      withoutRetiredScopes(['config:read', 'config:write', 'kb:read']),
    ).to.deep.equal(['config:read', 'kb:read']);
  });

  describe('OAuth apps', () => {
    const appId = new Types.ObjectId().toString();
    const orgId = new Types.ObjectId().toString();
    const userId = new Types.ObjectId().toString();
    let app: any;
    let tokens: {
      revokeAllTokensForApp: sinon.SinonStub;
      revokeRefreshTokensWithoutKnowledgeLimit: sinon.SinonStub;
    };

    const service = () => {
      tokens = {
        revokeAllTokensForApp: sinon.stub().resolves(),
        revokeRefreshTokensWithoutKnowledgeLimit: sinon.stub().resolves(),
      };
      const logger = {
        info: sinon.stub(),
        debug: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      };
      return new OAuthAppService(
        logger as any,
        { encrypt: sinon.stub().returns('enc'), decrypt: sinon.stub() } as any,
        scopes,
        tokens as any,
      );
    };

    beforeEach(() => {
      app = {
        _id: new Types.ObjectId(appId),
        clientId: 'client123',
        name: 'Old app',
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: ['config:write', 'kb:read'],
        save: sinon.stub().resolvesThis(),
      };
      sinon.stub(OAuthApp, 'findOne').resolves(app);
    });

    it('creating one with config:write is refused with the 400 before anything is stored', async () => {
      const create = sinon.stub(OAuthApp, 'create');
      try {
        await service().createApp(orgId, userId, true, {
          name: 'New',
          allowedScopes: ['config:write'],
        } as any);
        expect.fail('should have thrown');
      } catch (error: any) {
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.equal(RETIRED_SCOPE_MESSAGE);
      }
      expect(create.called).to.equal(false);
    });

    it('an app saved with it still saves when the scope is sent back, without it, and revokes nothing', async () => {
      const result = await service().updateApp(appId, orgId, userId, true, {
        allowedScopes: ['config:write', 'kb:read'],
      });
      expect(app.allowedScopes).to.deep.equal(['kb:read']);
      expect(result.allowedScopes).to.deep.equal(['kb:read']);
      expect(tokens.revokeAllTokensForApp.called).to.equal(false);
    });

    it('an app saved with it loses it on any save, and shows without it', async () => {
      const result = await service().updateApp(appId, orgId, userId, true, {
        name: 'Renamed',
      });
      expect(app.allowedScopes).to.deep.equal(['kb:read']);
      expect(result.allowedScopes).to.deep.equal(['kb:read']);
      expect(tokens.revokeAllTokensForApp.called).to.equal(false);
    });
  });
});
