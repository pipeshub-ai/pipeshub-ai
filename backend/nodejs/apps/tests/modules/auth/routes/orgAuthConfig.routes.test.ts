import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createOrgAuthConfigRouter } from '../../../../src/modules/auth/routes/orgAuthConfig.routes';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { ValidationError } from '../../../../src/libs/errors/validation.error';
import type { Request, Response, NextFunction } from 'express';

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
  const SAML_MESSAGE =
    "SAML single sign-on can't be combined with other sign-in steps yet. Use SAML on its own as a one-step sign-in, or remove it from the policy.";

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

  it('rejects a two-step policy that includes SAML and saves nothing', async () => {
    const { res, error } = await postPolicy([
      { order: 1, allowedMethods: [{ type: 'samlSso' }] },
      { order: 2, allowedMethods: [{ type: 'otp' }] },
    ]);

    expect(error).to.be.instanceOf(ValidationError);
    expect((error as ValidationError).statusCode).to.equal(400);
    expect((error as ValidationError).message).to.equal(SAML_MESSAGE);
    expect(findOne.callCount).to.equal(0);
    expect(savedDoc.save.callCount).to.equal(0);
    expect(res.status.callCount).to.equal(0);
  });

  it('still accepts a one-step policy offering SAML alongside other methods', async () => {
    const policy = [
      {
        order: 1,
        allowedMethods: [{ type: 'samlSso' }, { type: 'password' }],
      },
    ];
    const { res, error } = await postPolicy(policy);

    expect(error).to.equal(undefined);
    expect(savedDoc.save.callCount).to.equal(1);
    expect(savedDoc.authSteps).to.deep.equal(policy);
    expect(res.status.firstCall.args).to.deep.equal([200]);
  });

  it('still accepts a two-step policy without SAML', async () => {
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
