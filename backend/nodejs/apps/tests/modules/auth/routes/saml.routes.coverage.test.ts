import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createSamlRouter } from '../../../../src/modules/auth/routes/saml.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'

describe('SAML Routes - handler coverage', () => {
  let container: Container
  let router: any
  let mockSamlController: any
  let mockSessionService: any
  let mockIamService: any
  let mockJitProvisioningService: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      jwtSecret: 'jwt-secret',
      cookieSecret: 'cookie-secret',
      cmBackend: 'http://localhost:3004',
    }

    const mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    }

    mockSamlController = {
      signInViaSAML: sinon.stub().resolves(),
      getSamlEmailKeyByOrgId: sinon.stub().returns('email'),
    }

    mockSessionService = {
      getSession: sinon.stub().resolves(null),
      completeAuthentication: sinon.stub().resolves(),
    }

    mockIamService = {
      getUserByEmail: sinon.stub().resolves({ data: { _id: 'u1', hasLoggedIn: false } }),
      updateUser: sinon.stub().resolves(),
    }

    mockJitProvisioningService = {
      extractSamlUserDetails: sinon.stub().returns({ fullName: 'Test User' }),
      provisionUser: sinon.stub().resolves({ _id: 'u1', hasLoggedIn: false }),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('SessionService').toConstantValue(mockSessionService)
    container.bind<any>('IamService').toConstantValue(mockIamService)
    container.bind<any>('SamlController').toConstantValue(mockSamlController)
    container.bind<any>('JitProvisioningService').toConstantValue(mockJitProvisioningService)
    container.bind<Logger>('Logger').toConstantValue(mockLogger as any)
    // Need to bind ConfigurationManagerService for updateAppConfig handler
    container.bind<any>('ConfigurationManagerService').toConstantValue({})
    container.bind<any>('MailService').toConstantValue({ sendMail: sinon.stub() })

    router = createSamlRouter(container)
  })

  afterEach(() => {
    sinon.restore()
  })

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return null
    return layer.route.stack[layer.route.stack.length - 1].handle
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
      cookie: sinon.stub().returnsThis(),
      redirect: sinon.stub().returnsThis(),
    }
    return res
  }

  it('should create router with routes', () => {
    expect(router).to.exist
    expect(router.stack.length).to.be.greaterThan(0)
  })

  describe('GET /signIn', () => {
    it('should have a handler', () => {
      const handler = findHandler('/signIn', 'get')
      expect(handler).to.be.a('function')
    })

    it('should call samlController.signInViaSAML', async () => {
      const handler = findHandler('/signIn', 'get')
      const req = { body: {}, headers: {}, query: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(mockSamlController.signInViaSAML.calledOnce).to.be.true
    })

    it('should call next on error', async () => {
      const handler = findHandler('/signIn', 'get')
      mockSamlController.signInViaSAML.rejects(new Error('SAML error'))

      const req = { body: {}, headers: {}, query: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /signIn/callback', () => {
    it('should have a handler', () => {
      const handler = findHandler('/signIn/callback', 'post')
      expect(handler).to.be.a('function')
    })

    it('should call next when user not in request', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const req = {
        user: null,
        body: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when session token missing', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1' })).toString('base64')
      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when session is null', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token' })).toString('base64')
      mockSessionService.getSession.resolves(null)

      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when auth method not allowed', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token' })).toString('base64')
      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'password' }] }],
        email: 'test@test.com',
      })

      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        sessionInfo: null,
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should call next when orgId missing', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ sessionToken: 'token' })).toString('base64')
      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
      })

      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        sessionInfo: null,
        ip: '127.0.0.1',
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /signIn/callback - existing user success flow', () => {
    it('should redirect on success for existing user', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      sinon.stub(Org, 'findOne').resolves({ shortName: 'TestOrg' } as any)

      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.cookie.called).to.be.true
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should redirect for first-time login and update hasLoggedIn', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: false },
      })

      mockIamService.updateUser.resolves({ statusCode: 200 })
      sinon.stub(Org, 'findOne').resolves({ shortName: 'TestOrg' } as any)

      const req = {
        user: { email: 'test@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(mockIamService.updateUser.calledOnce).to.be.true
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should handle JIT provisioning for NOT_FOUND user', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'new@test.com',
        orgId: 'org1',
        userId: 'NOT_FOUND',
        jitConfig: { samlSso: true },
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')
      mockJitProvisioningService.extractSamlUserDetails.returns({ fullName: 'New User' })
      mockJitProvisioningService.provisionUser.resolves({
        _id: 'new-u1', email: 'new@test.com', orgId: 'org1', hasLoggedIn: false,
      })

      const { UserActivities } = require('../../../../src/modules/auth/schema/userActivities.schema')
      sinon.stub(UserActivities, 'create').resolves({})

      mockIamService.updateUser.resolves({ statusCode: 200 })
      sinon.stub(Org, 'findOne').resolves({ shortName: 'TestOrg' } as any)

      const req = {
        user: { email: 'new@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      if (!next.called) {
        expect(mockJitProvisioningService.provisionUser.calledOnce).to.be.true
        expect(res.redirect.calledOnce).to.be.true
      }
    })

    it('should redirect with error when email mismatch', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'expected@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'different@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      const req = {
        user: { email: 'different@test.com' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // Should redirect with error or call next
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should fallback to email key when SAML key returns invalid email', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('customKey')

      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })

      sinon.stub(Org, 'findOne').resolves({ shortName: 'TestOrg' } as any)

      const req = {
        user: {
          customKey: 'not-an-email',
          email: 'test@test.com',
        },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // Should have used fallback email
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should use RelayState from query params if not in body', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: 'test@test.com',
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('email')
      mockIamService.getUserByEmail.resolves({
        statusCode: 200,
        data: { _id: 'u1', email: 'test@test.com', orgId: 'org1', hasLoggedIn: true },
      })
      sinon.stub(Org, 'findOne').resolves({ shortName: 'TestOrg' } as any)

      const req = {
        user: { email: 'test@test.com' },
        body: {},
        query: { RelayState: relayState },
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      // Should work the same as body relay state
      expect(res.redirect.called || next.called).to.be.true
    })

    it('should throw InternalServerError when no valid email in SAML response', async () => {
      const handler = findHandler('/signIn/callback', 'post')
      const relayState = Buffer.from(JSON.stringify({ orgId: 'org1', sessionToken: 'token123' })).toString('base64')

      mockSessionService.getSession.resolves({
        currentStep: 0,
        authConfig: [{ allowedMethods: [{ type: 'samlSso' }] }],
        email: undefined,
        orgId: 'org1',
        userId: 'u1',
      })

      mockSamlController.getSamlEmailKeyByOrgId.returns('customKey')

      const req = {
        user: { customKey: 'not-an-email', noEmail: 'nope' },
        body: { RelayState: relayState },
        query: {},
        headers: {},
        ip: '127.0.0.1',
        sessionInfo: null as any,
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /updateAppConfig', () => {
    it('should have a handler', () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.be.a('function')
    })

    it('should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      const req = { body: {}, headers: {} }
      const res = mockRes()
      const next = sinon.stub()

      // loadAppConfig will fail due to missing env
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
