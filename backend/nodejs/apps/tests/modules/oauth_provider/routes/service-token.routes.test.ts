import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';
import { createServiceTokenRouter } from '../../../../src/modules/oauth_provider/routes/service-token.routes';

describe('Service token routes', () => {
  afterEach(() => {
    sinon.restore();
    delete process.env.TOKEN_RESTRICTIONS_MODE;
  });

  it('refuses OAuth and personal access tokens right after authentication', () => {
    const container = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'Logger')
          return {
            info: sinon.stub(),
            debug: sinon.stub(),
            warn: sinon.stub(),
            error: sinon.stub(),
          };
        if (key === 'AppConfig')
          return { maxOAuthClientRequestsPerMinute: 100 };
        if (key === 'AuthMiddleware')
          return { authenticate: function authenticate() {} };
        return {};
      }),
    };
    const router = createServiceTokenRouter(container as any);
    const names = (router.stack as any[])
      .filter((l) => !l.route)
      .map((l) => l.name);
    const authIdx = names.indexOf('bound authenticate');
    expect(authIdx).to.be.greaterThan(-1);
    expect(names[authIdx + 1]).to.equal('requireSessionAuthWhenEnforced');
  });

  it('refuses an OAuth token on every route when enforcing', async () => {
    process.env.TOKEN_RESTRICTIONS_MODE = 'enforce';
    const container = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'Logger')
          return {
            info: sinon.stub(),
            debug: sinon.stub(),
            warn: sinon.stub(),
            error: sinon.stub(),
          };
        if (key === 'AppConfig')
          return { maxOAuthClientRequestsPerMinute: 100 };
        if (key === 'AuthMiddleware')
          return { authenticate: function authenticate() {} };
        return {};
      }),
    };
    const router = createServiceTokenRouter(container as any);
    const guard = (router.stack as any[]).find(
      (l) => l.name === 'requireSessionAuthWhenEnforced',
    );
    expect(guard).to.exist;
    for (const user of [{ userId: 'u', isOAuth: true }]) {
      const next = sinon.stub();
      guard.handle({ user }, {}, next);
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);
    }
    const next = sinon.stub();
    guard.handle({ user: { userId: 'u' } }, {}, next);
    expect(next.firstCall.args).to.deep.equal([]);
  });

  it('lets an OAuth token through in log mode', () => {
    delete process.env.TOKEN_RESTRICTIONS_MODE;
    const container = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'Logger')
          return {
            info: sinon.stub(),
            debug: sinon.stub(),
            warn: sinon.stub(),
            error: sinon.stub(),
          };
        if (key === 'AppConfig')
          return { maxOAuthClientRequestsPerMinute: 100 };
        if (key === 'AuthMiddleware')
          return { authenticate: function authenticate() {} };
        return {};
      }),
    };
    const router = createServiceTokenRouter(container as any);
    const guard = (router.stack as any[]).find(
      (l) => l.name === 'requireSessionAuthWhenEnforced',
    );
    expect(guard).to.exist;
    const next = sinon.stub();
    guard.handle(
      { user: { userId: 'u', orgId: 'org-svc-log', isOAuth: true } },
      {},
      next,
    );
    expect(next.firstCall.args).to.deep.equal([]);
  });
});
