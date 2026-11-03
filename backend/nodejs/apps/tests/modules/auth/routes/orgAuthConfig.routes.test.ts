import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import {
  createOrgAuthConfigRouter,
  SAML_IN_MULTI_STEP_POLICY,
  signInMethodRemoved,
  signInMethodRenamed,
} from '../../../../src/modules/auth/routes/orgAuthConfig.routes';
import * as signInRemoval from '../../../../src/modules/auth/services/signInRemoval.service';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { ValidationError } from '../../../../src/libs/errors/validation.error';
import type { Request, Response, NextFunction } from 'express';
import { useCommunitySignInLists } from '../../../helpers/sign-in-lists';

describe('createOrgAuthConfigRouter', () => {
  let container: Container;
  let mockUserAccountController: any;
  let mockConfig: any;

  beforeEach(() => {
    container = new Container();

    mockUserAccountController = {
      getAuthMethod: sinon.stub(),
      setUpAuthConfig: sinon.stub(),
      updateAuthMethod: sinon.stub(),
    };

    mockConfig = {
      jwtSecret: 'test-secret',
      scopedJwtSecret: 'test-scoped',
    };


    container
      .bind<UserAccountController>('UserAccountController')
      .toConstantValue(mockUserAccountController);
    container
      .bind<AppConfig>('AppConfig')
      .toConstantValue(mockConfig as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return an Express router', () => {
    const router = createOrgAuthConfigRouter(container);
    expect(router).to.exist;
    expect(router).to.have.property('stack');
  });

  it('should register GET /authMethods route', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const authMethodsRoute = routes.find(
      (r: any) => r.path === '/authMethods',
    );
    expect(authMethodsRoute).to.exist;
    expect(authMethodsRoute?.methods.get).to.be.true;
  });

  it('should register POST / route for setup', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const setupRoute = routes.find((r: any) => r.path === '/');
    expect(setupRoute).to.exist;
    expect(setupRoute?.methods.post).to.be.true;
  });

  it('should register POST /updateAuthMethod route', () => {
    const router = createOrgAuthConfigRouter(container);
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }));

    const updateRoute = routes.find(
      (r: any) => r.path === '/updateAuthMethod',
    );
    expect(updateRoute).to.exist;
    expect(updateRoute?.methods.post).to.be.true;
  });

  it('should have attachContainerMiddleware as a non-route middleware', () => {
    const router = createOrgAuthConfigRouter(container);
    const middlewareLayers = router.stack.filter(
      (layer: any) => !layer.route,
    );
    expect(middlewareLayers.length).to.be.greaterThanOrEqual(1);
  });
});

describe('POST /updateAuthMethod policy validation', () => {
  useCommunitySignInLists();

  let container: Container;
  let savedDoc: { orgId: string; authSteps: unknown; save: sinon.SinonStub };
  let findOne: sinon.SinonStub;

  beforeEach(() => {
    container = new Container();
    const controller = new UserAccountController(
      ...([
        { scopedJwtSecret: 'test-scoped' },
        {
          checkAdminUser: sinon.stub().resolves({ statusCode: 200, data: {} }),
        },
        {},
        {},
        {},
        {
          info: sinon.stub(),
          debug: sinon.stub(),
          warn: sinon.stub(),
          error: sinon.stub(),
        },
        {},
      ] as unknown as ConstructorParameters<typeof UserAccountController>),
    );
    container
      .bind<UserAccountController>('UserAccountController')
      .toConstantValue(controller);

    savedDoc = { orgId: 'o1', authSteps: [], save: sinon.stub().resolves() };
    findOne = sinon.stub(OrgAuthConfig, 'findOne').resolves(savedDoc as never);
  });

  afterEach(() => {
    sinon.restore();
  });

  // Starts after userValidator and adminValidator, which need a live token and
  // session; req.user is set here the way they would set it.
  async function postPolicy(authMethod: unknown): Promise<{
    res: { status: sinon.SinonStub; json: sinon.SinonStub };
    error: unknown;
  }> {
    const router = createOrgAuthConfigRouter(container);
    const layer = router.stack.find(
      (l) => l.route?.path === '/updateAuthMethod',
    );
    if (!layer?.route) throw new Error('updateAuthMethod route not registered');
    const handlers = layer.route.stack.slice(2).map((l) => l.handle);

    const req = {
      user: { orgId: 'o1', userId: 'u1' },
      body: { authMethod },
      query: {},
      params: {},
      headers: {},
      path: '/updateAuthMethod',
      method: 'POST',
    };
    const res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    };
    let error: unknown;
    for (const handle of handlers) {
      const step = { proceeded: false };
      const next: NextFunction = (err?: unknown) => {
        if (err === undefined) step.proceeded = true;
        else error = err;
      };
      await handle(req as unknown as Request, res as unknown as Response, next);
      if (!step.proceeded) break;
    }
    return { res, error };
  }

  for (const removed of ['samlSso', 'oauth']) {
    it(`refuses a policy offering ${removed}, which 1.0 removed, and saves nothing`, async () => {
      const { res, error } = await postPolicy([
        { order: 1, allowedMethods: [{ type: removed }, { type: 'password' }] },
      ]);

      expect(error).to.be.instanceOf(ValidationError);
      expect((error as ValidationError).statusCode).to.equal(400);
      expect((error as ValidationError).message).to.equal(signInMethodRemoved(removed));
      expect(findOne.callCount).to.equal(0);
      expect(savedDoc.save.callCount).to.equal(0);
      expect(res.status.callCount).to.equal(0);
    });
  }

  it('refuses SAML in a two-step policy as removed, and under the multi-step rule', async () => {
    const { error } = await postPolicy([
      { order: 1, allowedMethods: [{ type: 'samlSso' }] },
      { order: 2, allowedMethods: [{ type: 'otp' }] },
    ]);

    const { message } = error as ValidationError;
    expect(message).to.include(signInMethodRemoved('samlSso'));
    expect(message).to.include(SAML_IN_MULTI_STEP_POLICY);
    expect(savedDoc.save.callCount).to.equal(0);
  });

  it('refuses Azure AD and names Microsoft instead', async () => {
    const { error } = await postPolicy([
      { order: 1, allowedMethods: [{ type: 'azureAd' }] },
    ]);

    expect(error).to.be.instanceOf(ValidationError);
    expect((error as ValidationError).message).to.equal(
      signInMethodRenamed('azureAd', 'microsoft'),
    );
    expect(savedDoc.save.callCount).to.equal(0);
  });

  it('accepts a two-step policy of methods that stay', async () => {
    const policy = [
      { order: 1, allowedMethods: [{ type: 'password' }] },
      { order: 2, allowedMethods: [{ type: 'otp' }] },
    ];
    const { res, error } = await postPolicy(policy);

    expect(error).to.equal(undefined);
    expect(savedDoc.save.callCount).to.equal(1);
    expect(savedDoc.authSteps).to.deep.equal(policy);
    expect(res.status.firstCall.args).to.deep.equal([200]);
  });
});

describe('GET /signInRemoval', () => {
  afterEach(() => {
    sinon.restore();
  });

  function handler() {
    const container = new Container();
    container
      .bind<UserAccountController>('UserAccountController')
      .toConstantValue({} as UserAccountController);
    const layer = createOrgAuthConfigRouter(container).stack.find(
      (l) => l.route?.path === '/signInRemoval' && (l.route as any).methods.get,
    );
    if (!layer?.route) throw new Error('signInRemoval route not registered');
    const handlers = layer.route.stack.map((l) => l.handle);
    return handlers[handlers.length - 1];
  }

  it("answers with this workspace's notice", async () => {
    const notice = { removedMethods: ['samlSso'], peopleWithoutPassword: [] };
    const find = sinon.stub(signInRemoval, 'findSignInRemovalNotice').resolves(notice);
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() };
    const next = sinon.stub();

    await handler()({ user: { orgId: 'o1' } } as unknown as Request, res as unknown as Response, next);

    expect(find.calledOnceWithExactly('o1')).to.be.true;
    expect(res.status.calledWith(200)).to.be.true;
    expect(res.json.calledWith(notice)).to.be.true;
    expect(next.called).to.be.false;
  });

  it('passes a failed lookup to next', async () => {
    sinon.stub(signInRemoval, 'findSignInRemovalNotice').rejects(new Error('db down'));
    const next = sinon.stub();

    await handler()({ user: { orgId: 'o1' } } as unknown as Request, {} as Response, next);

    expect(next.calledOnce).to.be.true;
  });
});
