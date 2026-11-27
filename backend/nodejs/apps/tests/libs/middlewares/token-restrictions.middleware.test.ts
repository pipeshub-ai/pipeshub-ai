import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Logger } from '../../../src/libs/services/logger.service';
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum';
import { ForbiddenError } from '../../../src/libs/errors/http.errors';
import { requireScopesWhenEnforced } from '../../../src/libs/middlewares/require-scopes.middleware';
import { requireSessionAuthWhenEnforced } from '../../../src/libs/middlewares/require-session-auth.middleware';

describe('token restriction middlewares', () => {
  const saved = process.env.TOKEN_RESTRICTIONS_MODE;
  let warn: sinon.SinonStub;

  beforeEach(() => {
    warn = sinon.stub(Logger.getInstance(), 'warn');
    delete process.env.TOKEN_RESTRICTIONS_MODE;
  });

  afterEach(() => {
    sinon.restore();
    if (saved === undefined) delete process.env.TOKEN_RESTRICTIONS_MODE;
    else process.env.TOKEN_RESTRICTIONS_MODE = saved;
  });

  const token = (scopes: string[], orgId: string) =>
    ({
      user: { isOAuth: true, oauthScopes: scopes, orgId },
      method: 'GET',
      baseUrl: '/b',
    }) as any;
  const session = { user: { userId: 'u', orgId: 'o' } } as any;

  describe('requireScopesWhenEnforced', () => {
    const guard = requireScopesWhenEnforced(OAuthScopeNames.CONFIG_READ);

    it('lets a token without the scope through in log mode and records it', () => {
      const next = sinon.stub();
      guard(token(['user:read'], 'org-scope-log'), {} as any, next);
      expect(next.calledOnceWithExactly()).to.equal(true);
      expect(warn.firstCall.args[1]).to.include({
        securityEvent: 'token_restriction_observed',
        reason: 'missing_scope',
      });
    });

    it('does not record a token that has the scope, or a session', () => {
      const next = sinon.stub();
      guard(
        token([OAuthScopeNames.CONFIG_READ], 'org-scope-ok'),
        {} as any,
        next,
      );
      guard(session, {} as any, next);
      expect(next.callCount).to.equal(2);
      expect(warn.called).to.equal(false);
    });

    it('refuses a token without the scope when enforcing', () => {
      process.env.TOKEN_RESTRICTIONS_MODE = 'enforce';
      const next = sinon.stub();
      guard(token(['user:read'], 'org-scope-enf'), {} as any, next);
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);
      expect(warn.called).to.equal(false);
    });
  });

  describe('requireSessionAuthWhenEnforced', () => {
    it('lets a token through in log mode and records it', () => {
      const next = sinon.stub();
      requireSessionAuthWhenEnforced(
        token([], 'org-sess-log'),
        {} as any,
        next,
      );
      expect(next.calledOnceWithExactly()).to.equal(true);
      expect(warn.firstCall.args[1]).to.include({
        securityEvent: 'token_restriction_observed',
        reason: 'session_only_route',
      });
    });

    it('does not record a session', () => {
      const next = sinon.stub();
      requireSessionAuthWhenEnforced(session, {} as any, next);
      expect(next.calledOnceWithExactly()).to.equal(true);
      expect(warn.called).to.equal(false);
    });

    it('refuses a token when enforcing', () => {
      process.env.TOKEN_RESTRICTIONS_MODE = 'enforce';
      const next = sinon.stub();
      requireSessionAuthWhenEnforced(
        token([], 'org-sess-enf'),
        {} as any,
        next,
      );
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);
    });
  });
});
