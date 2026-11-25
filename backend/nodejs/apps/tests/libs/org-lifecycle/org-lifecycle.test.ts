import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import {
  DEFAULT_ORG_LIFECYCLE,
  defaultScopedTokenGuard,
  installOrgLifecycle,
  noOrgLifecycleHook,
  orgLifecycle,
  resetOrgLifecycle,
} from '../../../src/libs/org-lifecycle';
import {
  orgLifecycleHook,
  scopedTokenGuard,
} from '../../../src/config';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { ForbiddenError } from '../../../src/libs/errors/http.errors';

const LIMITED_SCOPE = 'limited:only';

const tokenWith = (claims: Record<string, unknown>): string =>
  jwt.sign(claims, 'test-secret');

describe('org lifecycle extension points', () => {
  // Mocha runs files in parallel workers that share module state: a file that ran App.initialize in this worker
  // installed the app's lifecycle (the same values, another object), so each test starts from the defaults.
  beforeEach(() => resetOrgLifecycle());
  afterEach(() => {
    resetOrgLifecycle();
    sinon.restore();
  });

  it('by default an organization is deleted at once and no token is claimed: no deletion hook, a guard that answers false', () => {
    // Edition-blind: config.ts may bind an edition's hooks; the shared suite checks the defaults and their shape.
    expect(noOrgLifecycleHook).to.equal(null);
    expect(defaultScopedTokenGuard('anything')).to.equal(false);
    expect(scopedTokenGuard).to.be.a('function');
    expect(orgLifecycleHook === null || typeof orgLifecycleHook === 'object').to.equal(true);
    expect(orgLifecycle()).to.equal(DEFAULT_ORG_LIFECYCLE);
    expect(orgLifecycle().orgLifecycleHook).to.equal(null);
    expect(
      orgLifecycle().scopedTokenGuard(tokenWith({ scopes: [LIMITED_SCOPE] })),
    ).to.equal(false);
  });

  it('an installed lifecycle replaces only what it names, and reset puts the defaults back', () => {
    const guard = () => false as const;
    installOrgLifecycle({ scopedTokenGuard: guard });
    expect(orgLifecycle().scopedTokenGuard).to.equal(guard);
    expect(orgLifecycle().orgLifecycleHook).to.equal(null);
    resetOrgLifecycle();
    expect(orgLifecycle()).to.equal(DEFAULT_ORG_LIFECYCLE);
  });

  describe('the auth middleware', () => {
    const run = async (token: string) => {
      const tokenService = sinon.createStubInstance(AuthTokenService);
      tokenService.verifyUserActionToken.resolves({ scopes: [LIMITED_SCOPE] } as never);
      tokenService.verifyToken.rejects(new Error('not a session token'));
      const middleware = new AuthMiddleware(
        sinon.createStubInstance(Logger) as unknown as Logger,
        tokenService as unknown as AuthTokenService,
        () => null,
      );
      const next = sinon.stub();
      await middleware.authenticate(
        { headers: { authorization: `Bearer ${token}` }, path: '/x', get: sinon.stub() } as never,
        {} as never,
        next,
      );
      return { next, tokenService };
    };

    it('with the default guard, a token is verified as any session token is', async () => {
      const { next, tokenService } = await run(tokenWith({ scopes: [LIMITED_SCOPE] }));
      expect(tokenService.verifyUserActionToken.called).to.equal(false);
      expect(tokenService.verifyToken.calledOnce).to.equal(true);
      expect(next.firstCall.args[0]).to.not.be.instanceOf(ForbiddenError);
    });

    it('a token the installed guard claims is checked genuine against its scope, then refused with its reason', async () => {
      installOrgLifecycle({
        scopedTokenGuard: (token) =>
          (jwt.decode(token) as { scopes?: string[] } | null)?.scopes?.includes(LIMITED_SCOPE)
            ? { scope: LIMITED_SCOPE, message: 'Only its own routes.', reasonCode: 'limited_only' }
            : false,
      });
      const { next, tokenService } = await run(tokenWith({ scopes: [LIMITED_SCOPE] }));
      expect(tokenService.verifyUserActionToken.calledOnceWith(sinon.match.string, LIMITED_SCOPE)).to.equal(true);
      expect(tokenService.verifyToken.called).to.equal(false);
      const refused = next.firstCall.args[0];
      expect(refused).to.be.instanceOf(ForbiddenError);
      expect(refused.message).to.equal('Only its own routes.');
      expect(refused.metadata).to.deep.equal({ reasonCode: 'limited_only' });
    });

    it('a forged token the guard claims fails its verification and is never refused as limited', async () => {
      installOrgLifecycle({
        scopedTokenGuard: () => ({ scope: LIMITED_SCOPE, message: 'm', reasonCode: 'r' }),
      });
      const tokenService = sinon.createStubInstance(AuthTokenService);
      tokenService.verifyUserActionToken.rejects(new Error('invalid signature'));
      const middleware = new AuthMiddleware(
        sinon.createStubInstance(Logger) as unknown as Logger,
        tokenService as unknown as AuthTokenService,
        () => null,
      );
      const next = sinon.stub();
      await middleware.authenticate(
        { headers: { authorization: `Bearer ${tokenWith({})}` }, path: '/x', get: sinon.stub() } as never,
        {} as never,
        next,
      );
      expect(next.firstCall.args[0]).to.not.be.instanceOf(ForbiddenError);
    });
  });
});
