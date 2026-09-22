import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  ServiceTokenService,
  SERVICE_TOKEN_DENIED_SCOPES,
  SERVICE_TOKEN_DEFAULT_EXPIRY_DAYS,
  SERVICE_TOKEN_MAX_EXPIRY_DAYS,
  SERVICE_TOKEN_LIST_LIMIT,
  SERVICE_TOKEN_MAX_ACTIVE,
} from '../../../../src/modules/oauth_provider/services/service-token.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { SERVICE_TOKEN_PREFIX } from '../../../../src/modules/oauth_provider/constants/constants';

const MCP_SCOPES = [
  'openid',
  'kb:read',
  'semantic:write',
  'conversation:chat',
  'user:read',
  'agent:execute',
];

function makeService(overrides: { mcpScopes?: string[] } = {}) {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  const encryption = { encrypt: sinon.stub().returns('encrypted') };
  const config = {
    getMcpScopes: sinon.stub().resolves(overrides.mcpScopes ?? MCP_SCOPES),
  };
  const tokens = {
    generateTokens: sinon.stub().resolves({
      accessToken: 'header.payload.signature',
      accessTokenId: 'token-id-1',
    }),
    listAccessTokensForUser: sinon.stub().resolves([]),
    revokeAccessTokenById: sinon.stub().resolves(true),
    revokeAllTokensForUser: sinon.stub().resolves(),
    revokeEveryTokenForUser: sinon.stub().resolves(),
    countActiveAccessTokensForUser: sinon.stub().resolves(0),
  };
  const scopeValidator = { validateScopesForApp: sinon.stub() };
  return {
    service: new ServiceTokenService(
      logger as any,
      encryption as any,
      config as any,
      tokens as any,
      scopeValidator as any,
    ),
    tokens,
    scopeValidator,
  };
}

function stubServiceAccount(doc: Record<string, unknown> | null) {
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().returns({ exec: sinon.stub().resolves(doc) }),
    }),
  } as any);
}

describe('ServiceTokenService', () => {
  const orgId = new mongoose.Types.ObjectId().toString();
  const adminId = new mongoose.Types.ObjectId().toString();
  const accountId = new mongoose.Types.ObjectId().toString();

  afterEach(() => sinon.restore());

  describe('getAvailableScopes', () => {
    it('offers the instance scopes without the ones version one refuses', async () => {
      const { service } = makeService();
      const scopes = await service.getAvailableScopes();

      expect(scopes).to.include('kb:read');
      expect(scopes).to.not.include('agent:execute');
    });

    it('keeps semantic:write, which is what running a search requires', async () => {
      // Named "write", but it is a read operation. Filtering scopes by the
      // word in their name would remove reading and leave writing alone.
      const { service } = makeService();
      expect(await service.getAvailableScopes()).to.include('semantic:write');
      expect(SERVICE_TOKEN_DENIED_SCOPES).to.not.include('semantic:write');
    });
  });

  describe('createToken', () => {
    beforeEach(() => {
      stubServiceAccount({ fullName: 'Nightly sync', isDisabled: false });
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: 'x' } as any);
    });

    it('refuses an empty scope list instead of granting everything', async () => {
      const { service } = makeService();
      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: [],
        });
        expect.fail('expected an empty scope list to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('at least one scope');
      }
    });

    it('refuses agent:execute, which is what makes version one read-only', async () => {
      const { service } = makeService();
      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: ['kb:read', 'agent:execute'],
        });
        expect.fail('expected agent:execute to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('read-only');
      }
    });

    it('refuses a scope the instance does not allow', async () => {
      const { service } = makeService();
      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: ['kb:read', 'made:up'],
        });
        expect.fail('expected an unknown scope to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('made:up');
      }
    });

    it('issues the token to the service account, not to the admin minting it', async () => {
      const { service, tokens } = makeService();
      await service.createToken(orgId, adminId, {
        serviceAccountId: accountId,
        name: 'nightly',
        scopes: ['kb:read'],
      });

      const args = tokens.generateTokens.firstCall.args;
      expect(args[1]).to.equal(accountId);
      expect(args[1]).to.not.equal(adminId);
    });

    it('marks the token with its own prefix, not the personal one', async () => {
      const { service } = makeService();
      const token = await service.createToken(orgId, adminId, {
        serviceAccountId: accountId,
        name: 'nightly',
        scopes: ['kb:read'],
      });

      expect(token.accessToken.startsWith(SERVICE_TOKEN_PREFIX)).to.equal(true);
      expect(token.accessToken.startsWith('phpat_')).to.equal(false);
    });

    it('defaults to 90 days and refuses more than a year', async () => {
      const { service, tokens } = makeService();
      await service.createToken(orgId, adminId, {
        serviceAccountId: accountId,
        name: 'nightly',
        scopes: ['kb:read'],
      });
      expect(
        tokens.generateTokens.firstCall.args[7]
          .accessTokenLifetimeOverrideSeconds,
      ).to.equal(SERVICE_TOKEN_DEFAULT_EXPIRY_DAYS * 86400);

      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: ['kb:read'],
          expiryDays: SERVICE_TOKEN_MAX_EXPIRY_DAYS + 1,
        });
        expect.fail('expected an over-long expiry to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('cannot exceed');
      }
    });
  });

  describe('the number of live tokens is bounded', () => {
    it('refuses a new token once the account is at the cap', async () => {
      // Bounding issuance is what keeps the list complete: no account can
      // hold more tokens than an administrator can be shown, so none can hide
      // from revocation.
      stubServiceAccount({ fullName: 'Nightly sync', isDisabled: false });
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: 'x' } as any);
      const { service, tokens } = makeService();
      tokens.countActiveAccessTokensForUser.resolves(SERVICE_TOKEN_MAX_ACTIVE);

      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'one too many',
          scopes: ['kb:read'],
        });
        expect.fail('expected the cap to be enforced');
      } catch (error) {
        expect((error as Error).message).to.contain('already holds');
      }
      expect(tokens.generateTokens.called).to.equal(false);
    });

    it('stays well under the number the list can return', () => {
      expect(SERVICE_TOKEN_MAX_ACTIVE).to.be.lessThan(SERVICE_TOKEN_LIST_LIMIT);
    });
  });

  describe('listTokens', () => {
    it('asks for every token, not the first page', async () => {
      // This list is what an administrator revokes from. A token missing from
      // it is a credential nobody can switch off.
      stubServiceAccount({ fullName: 'Nightly sync', isDisabled: false });
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: 'x' } as any);
      const { service, tokens } = makeService();

      await service.listTokens(orgId, accountId);

      expect(tokens.listAccessTokensForUser.calledOnce).to.equal(true);
      expect(tokens.listAccessTokensForUser.firstCall.args[2]).to.equal(
        SERVICE_TOKEN_LIST_LIMIT,
      );
    });
  });

  describe('revokeAllForServiceAccount', () => {
    it('revokes everything the account holds, without caring that it is gone', async () => {
      // Called when an account is deleted and again if it is restored, so it
      // deliberately does not require the account to exist or be enabled.
      const { service, tokens } = makeService();

      await service.revokeAllForServiceAccount(orgId, accountId);

      expect(tokens.revokeEveryTokenForUser.calledOnce).to.equal(true);
      expect(tokens.revokeEveryTokenForUser.firstCall.args[0]).to.equal(
        accountId,
      );
    });

    it('reaches tokens issued under other clients, not just the service-token app', async () => {
      // A credential stored under pat-system: or an app's own clientId is
      // still one this identity holds. Restoring the account reuses the
      // record, so anything left alive would start working for whoever
      // reused the name.
      const { service, tokens } = makeService();

      await service.revokeAllForServiceAccount(orgId, accountId);

      expect(tokens.revokeEveryTokenForUser.calledOnce).to.equal(true);
      // Keyed on the user alone: no clientId narrows it.
      expect(tokens.revokeEveryTokenForUser.firstCall.args).to.have.length(1);
      expect(tokens.revokeAllTokensForUser.called).to.equal(false);
    });
  });

  describe('the target must be a service account', () => {
    it('will not mint a token that authenticates as a colleague', async () => {
      const { service } = makeService();
      // A human's id finds nothing, because kind is part of the query.
      const findOne = stubServiceAccount(null);

      try {
        await service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: ['kb:read'],
        });
        expect.fail('expected a non-service account to be refused');
      } catch (error) {
        expect((error as Error).message).to.equal('Service account not found');
      }
      expect(findOne.firstCall.args[0]).to.include({
        kind: 'service',
        orgId,
        isDeleted: false,
      });
    });

    it('refuses to mint for a disabled account, but still lists its tokens', async () => {
      stubServiceAccount({ fullName: 'Old job', isDisabled: true });
      sinon.stub(OAuthApp, 'findOne').resolves({ clientId: 'x' } as any);

      const minting = makeService();
      try {
        await minting.service.createToken(orgId, adminId, {
          serviceAccountId: accountId,
          name: 'nightly',
          scopes: ['kb:read'],
        });
        expect.fail('expected minting for a disabled account to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('disabled');
      }

      // Listing has to keep working, or an admin who just disabled an account
      // could not see the tokens it still holds in order to revoke them.
      const listing = makeService();
      expect(await listing.service.listTokens(orgId, accountId)).to.deep.equal(
        [],
      );
    });
  });
});
