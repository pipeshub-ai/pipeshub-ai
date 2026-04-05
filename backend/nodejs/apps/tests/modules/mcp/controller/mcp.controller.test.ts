import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createMockRequest, createMockResponse, createMockNext, createAuthenticatedRequest } from '../../../helpers/mock-request'
import { createMockAppConfig } from '../../../helpers/fixtures/config.fixture'
import { Logger } from '../../../../src/libs/services/logger.service'

// The controller is imported AFTER mock-mcp-global.ts has patched require.cache
// for @pipeshub-ai/mcp/* and @modelcontextprotocol/sdk, so all ESM deps resolve
// to our fakes.
import { handleMCPRequest } from '../../../../src/modules/mcp/controller/mcp.controller'

// Cached module references — we mutate these exports per-test so the controller
// picks up our stubs (it destructures from the same object on every request).
const mcpServerExports = require.cache[
  require.resolve('@pipeshub-ai/mcp/esm/mcp-server/server.js')
]!.exports
const mcpCoreExports = require.cache[
  require.resolve('@pipeshub-ai/mcp/esm/core.js')
]!.exports
const sdkTransportExports = require.cache[
  require.resolve('@modelcontextprotocol/sdk/server/streamableHttp.js')
]!.exports

describe('MCP Controller — handleMCPRequest', () => {
  let appConfig: any

  // Save originals so we can restore after each test
  let origCreateMCPServer: any
  let origPipeshubCore: any
  let origTransport: any

  beforeEach(() => {
    appConfig = createMockAppConfig()

    // Preserve originals
    origCreateMCPServer = mcpServerExports.createMCPServer
    origPipeshubCore = mcpCoreExports.PipeshubCore
    origTransport = sdkTransportExports.StreamableHTTPServerTransport
  })

  afterEach(() => {
    // Restore original mocks
    mcpServerExports.createMCPServer = origCreateMCPServer
    mcpCoreExports.PipeshubCore = origPipeshubCore
    sdkTransportExports.StreamableHTTPServerTransport = origTransport
    sinon.restore()
  })

  // =========================================================================
  // Curried function shape
  // =========================================================================
  describe('function shape', () => {
    it('should return a handler function when called with appConfig', () => {
      const handler = handleMCPRequest(appConfig)
      expect(handler).to.be.a('function')
    })

    it('should return an async function (returns promise)', async () => {
      const handler = handleMCPRequest(appConfig)
      const req = createMockRequest({ headers: { authorization: 'Bearer tok' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()
      const result = handler(req, res as any, next)
      expect(result).to.be.instanceOf(Promise)
      await result
    })
  })

  // =========================================================================
  // Token extraction
  // =========================================================================
  describe('token extraction', () => {
    it('should extract Bearer token from Authorization header', async () => {
      let capturedOpts: any
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { capturedOpts = opts }
      }
      const connectStub = sinon.stub().resolves()
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: connectStub } }
      })

      const req = createMockRequest({
        headers: { authorization: 'Bearer my-secret-token-123' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedOpts.security.bearerAuth).to.equal('my-secret-token-123')
    })

    it('should use empty string when Authorization header is missing', async () => {
      let capturedOpts: any
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { capturedOpts = opts }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedOpts.security.bearerAuth).to.equal('')
    })

    it('should handle Authorization header without Bearer prefix', async () => {
      let capturedOpts: any
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { capturedOpts = opts }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })

      const req = createMockRequest({
        headers: { authorization: 'Basic abc123' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      // 'Basic abc123'.replace('Bearer ', '') === 'Basic abc123' (no match)
      expect(capturedOpts.security.bearerAuth).to.equal('Basic abc123')
    })

    it('should handle Authorization header that is exactly "Bearer "', async () => {
      let capturedOpts: any
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { capturedOpts = opts }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })

      const req = createMockRequest({
        headers: { authorization: 'Bearer ' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedOpts.security.bearerAuth).to.equal('')
    })
  })

  // =========================================================================
  // serverURL construction
  // =========================================================================
  describe('serverURL construction', () => {
    it('should construct serverURL from appConfig.oauthBackendUrl + /api/v1', async () => {
      let capturedServerURL: string | undefined
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        capturedServerURL = opts.serverURL
        return { server: { connect: sinon.stub().resolves() } }
      })

      appConfig.oauthBackendUrl = 'https://my-backend.example.com'
      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedServerURL).to.equal('https://my-backend.example.com/api/v1')
    })

    it('should pass same serverURL to PipeshubCore', async () => {
      let coreServerURL: string | undefined
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { coreServerURL = opts.serverURL }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        // Invoke getSDK to trigger PipeshubCore instantiation
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })

      appConfig.oauthBackendUrl = 'http://localhost:3001'
      const req = createMockRequest({ headers: { authorization: 'Bearer t' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(coreServerURL).to.equal('http://localhost:3001/api/v1')
    })
  })

  // =========================================================================
  // StreamableHTTPServerTransport instantiation
  // =========================================================================
  describe('StreamableHTTPServerTransport', () => {
    it('should create transport with sessionIdGenerator: undefined', async () => {
      let capturedTransportOpts: any
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) { capturedTransportOpts = opts }
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedTransportOpts).to.have.property('sessionIdGenerator', undefined)
    })

    it('should pass transport instance to mcpServer.connect', async () => {
      let transportInstance: any
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() { transportInstance = this }
        handleRequest() { return Promise.resolve() }
      }
      const connectStub = sinon.stub().resolves()
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: connectStub },
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(connectStub.calledOnce).to.be.true
      expect(connectStub.firstCall.args[0]).to.equal(transportInstance)
    })

    it('should call transport.handleRequest with req, res, req.body', async () => {
      const handleRequestStub = sinon.stub().resolves()
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() {}
        handleRequest = handleRequestStub
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const body = { jsonrpc: '2.0', method: 'initialize', id: 1 }
      const req = createMockRequest({ headers: {}, body })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(handleRequestStub.calledOnce).to.be.true
      expect(handleRequestStub.firstCall.args[0]).to.equal(req)
      expect(handleRequestStub.firstCall.args[1]).to.equal(res)
      expect(handleRequestStub.firstCall.args[2]).to.equal(body)
    })
  })

  // =========================================================================
  // createMCPServer arguments
  // =========================================================================
  describe('createMCPServer configuration', () => {
    it('should call createMCPServer with dynamic: true', async () => {
      const createStub = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })
      mcpServerExports.createMCPServer = createStub

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(createStub.calledOnce).to.be.true
      expect(createStub.firstCall.args[0].dynamic).to.be.true
    })

    it('should call createMCPServer with correct serverURL', async () => {
      const createStub = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })
      mcpServerExports.createMCPServer = createStub
      appConfig.oauthBackendUrl = 'https://prod.example.com'

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(createStub.firstCall.args[0].serverURL).to.equal('https://prod.example.com/api/v1')
    })

    it('should pass logger with level, info, debug, warning, error functions', async () => {
      const createStub = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })
      mcpServerExports.createMCPServer = createStub

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      const loggerArg = createStub.firstCall.args[0].logger
      expect(loggerArg).to.have.property('level')
      expect(loggerArg.info).to.be.a('function')
      expect(loggerArg.debug).to.be.a('function')
      expect(loggerArg.warning).to.be.a('function')
      expect(loggerArg.error).to.be.a('function')
    })

    it('should pass getSDK factory that creates PipeshubCore', async () => {
      let sdkInstance: any
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { sdkInstance = { opts, instance: this } }
      }
      const createStub = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })
      mcpServerExports.createMCPServer = createStub

      const req = createMockRequest({
        headers: { authorization: 'Bearer test-tok' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(sdkInstance).to.exist
      expect(sdkInstance.opts.security.bearerAuth).to.equal('test-tok')
    })

    it('should provide getSDK factory that always creates a new PipeshubCore instance', async () => {
      const instances: any[] = []
      mcpCoreExports.PipeshubCore = class {
        constructor() { instances.push(this) }
      }
      const createStub = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })
      mcpServerExports.createMCPServer = createStub

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(instances).to.have.length(2)
      expect(instances[0]).to.not.equal(instances[1])
    })
  })

  // =========================================================================
  // Successful flow
  // =========================================================================
  describe('successful request flow', () => {
    it('should not call next on success', async () => {
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.called).to.be.false
    })

    it('should complete the full flow: import → transport → server → connect → handleRequest', async () => {
      const callOrder: string[] = []

      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() { callOrder.push('transport-created') }
        handleRequest() {
          callOrder.push('handle-request')
          return Promise.resolve()
        }
      }
      const connectStub = sinon.stub().callsFake(() => {
        callOrder.push('server-connected')
        return Promise.resolve()
      })
      mcpServerExports.createMCPServer = sinon.stub().callsFake(() => {
        callOrder.push('server-created')
        return { server: { connect: connectStub } }
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(callOrder).to.deep.equal([
        'transport-created',
        'server-created',
        'server-connected',
        'handle-request',
      ])
    })

    it('should work with POST request', async () => {
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({
        method: 'POST',
        headers: { authorization: 'Bearer tok' },
        body: { jsonrpc: '2.0', method: 'tools/list', id: 1 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.called).to.be.false
    })

    it('should work with GET request', async () => {
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({
        method: 'GET',
        headers: { authorization: 'Bearer tok' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.called).to.be.false
    })
  })

  // =========================================================================
  // Error handling — negative tests
  // =========================================================================
  describe('error handling', () => {
    // Stub the singleton instance directly — Logger.getInstance() always
    // returns the same object, so stubbing its `error` method ensures we
    // intercept calls from the controller (which holds a reference to the
    // same singleton).
    let errorStub: sinon.SinonStub
    const loggerInstance = Logger.getInstance()

    beforeEach(() => {
      // Defensively restore any lingering stub
      if (typeof (loggerInstance.error as any).restore === 'function') {
        (loggerInstance.error as any).restore()
      }
      errorStub = sinon.stub(loggerInstance, 'error')
    })

    it('should call next(error) when createMCPServer throws', async () => {
      const error = new Error('createMCPServer failed')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should call next(error) when mcpServer.connect rejects', async () => {
      const error = new Error('connect failed')
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().rejects(error) },
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should call next(error) when transport.handleRequest rejects', async () => {
      const error = new Error('handleRequest failed')
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() {}
        handleRequest() { return Promise.reject(error) }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should call next(error) when StreamableHTTPServerTransport constructor throws', async () => {
      const error = new Error('transport constructor failed')
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() { throw error }
      }

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should call next(error) when PipeshubCore constructor throws inside getSDK', async () => {
      const error = new Error('PipeshubCore init failed')
      mcpCoreExports.PipeshubCore = class {
        constructor() { throw error }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK() // This triggers PipeshubCore constructor → throws
        return { server: { connect: sinon.stub().resolves() } }
      })

      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should log error with error.message, req.method, and req.user.userId', async () => {
      const error = new Error('test error message')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createAuthenticatedRequest('user-42', 'org-1', 'user@test.com', {
        method: 'POST',
        headers: { authorization: 'Bearer tok' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(errorStub.calledOnce).to.be.true
      expect(errorStub.firstCall.args[0]).to.equal('MCP request failed')
      expect(errorStub.firstCall.args[1]).to.deep.include({
        error: 'test error message',
        method: 'POST',
        userId: 'user-42',
      })
    })

    it('should log userId as undefined when req.user is not set', async () => {
      const error = new Error('no-user error')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createMockRequest({
        method: 'GET',
        headers: {},
        body: {},
        user: undefined,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(errorStub.calledOnce).to.be.true
      expect(errorStub.firstCall.args[1].userId).to.be.undefined
    })

    it('should log userId as undefined when req.user exists but has no userId', async () => {
      const error = new Error('partial user')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createMockRequest({
        method: 'POST',
        headers: {},
        body: {},
        user: { email: 'x@y.com' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(errorStub.firstCall.args[1].userId).to.be.undefined
    })

    it('should log the request method in error metadata', async () => {
      const error = new Error('method test')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createMockRequest({ method: 'DELETE', headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(errorStub.firstCall.args[1].method).to.equal('DELETE')
    })
  })

  // =========================================================================
  // Multiple sequential requests
  // =========================================================================
  describe('multiple requests', () => {
    it('should handle multiple sequential requests independently', async () => {
      const tokens: string[] = []
      mcpCoreExports.PipeshubCore = class {
        constructor(opts: any) { tokens.push(opts.security.bearerAuth) }
      }
      mcpServerExports.createMCPServer = sinon.stub().callsFake((opts: any) => {
        opts.getSDK()
        return { server: { connect: sinon.stub().resolves() } }
      })

      const handler = handleMCPRequest(appConfig)

      const req1 = createMockRequest({ headers: { authorization: 'Bearer token-a' }, body: {} })
      const req2 = createMockRequest({ headers: { authorization: 'Bearer token-b' }, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req1, res as any, next)
      await handler(req2, res as any, next)

      expect(tokens).to.deep.equal(['token-a', 'token-b'])
    })

    it('should create a new transport for each request', async () => {
      const transports: any[] = []
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor() { transports.push(this) }
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const handler = handleMCPRequest(appConfig)
      const res = createMockResponse()
      const next = createMockNext()

      await handler(createMockRequest({ headers: {}, body: {} }), res as any, next)
      await handler(createMockRequest({ headers: {}, body: {} }), res as any, next)

      expect(transports).to.have.length(2)
      expect(transports[0]).to.not.equal(transports[1])
    })
  })
})
