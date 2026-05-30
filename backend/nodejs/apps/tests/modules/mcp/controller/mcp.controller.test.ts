import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createMockRequest, createMockResponse, createMockNext } from '../../../helpers/mock-request'
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

  // A valid MCP initialize request body — passes isInitializeRequest() check
  const initBody = { jsonrpc: '2.0', method: 'initialize', id: 1 }

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
      const req = createMockRequest({ headers: { authorization: 'Bearer tok' }, body: initBody })
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
        body: initBody,
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

      const req = createMockRequest({ headers: {}, body: initBody })
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
        body: initBody,
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
        body: initBody,
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
      const req = createMockRequest({ headers: {}, body: initBody })
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
      const req = createMockRequest({ headers: { authorization: 'Bearer t' }, body: initBody })
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
    it('should create transport with a sessionIdGenerator function', async () => {
      let capturedTransportOpts: any
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          capturedTransportOpts = opts
          if (opts?.onsessioninitialized) opts.onsessioninitialized('test-sid')
        }
        sessionId = 'test-sid'
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedTransportOpts).to.have.property('sessionIdGenerator')
      expect(capturedTransportOpts.sessionIdGenerator).to.be.a('function')
      const generated = capturedTransportOpts.sessionIdGenerator()
      expect(generated).to.be.a('string').with.length.greaterThan(0)
    })

    it('should pass transport instance to mcpServer.connect', async () => {
      let transportInstance: any
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          transportInstance = this
          if (opts?.onsessioninitialized) opts.onsessioninitialized('sid-connect')
        }
        sessionId = 'sid-connect'
        handleRequest() { return Promise.resolve() }
      }
      const connectStub = sinon.stub().resolves()
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: connectStub },
      })

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(connectStub.calledOnce).to.be.true
      expect(connectStub.firstCall.args[0]).to.equal(transportInstance)
    })

    it('should call transport.handleRequest with req, res, req.body', async () => {
      const handleRequestStub = sinon.stub().resolves()
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          if (opts?.onsessioninitialized) opts.onsessioninitialized('sid-hr')
        }
        sessionId = 'sid-hr'
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

    it('should pass onsessioninitialized callback that stores transport by session ID', async () => {
      let capturedOnsessioninitialized: ((sid: string) => void) | undefined
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          capturedOnsessioninitialized = opts?.onsessioninitialized
        }
        sessionId = 'sid-store'
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedOnsessioninitialized).to.be.a('function')
    })

    it('should set onclose handler on the transport', async () => {
      let capturedTransport: any
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          capturedTransport = this
          if (opts?.onsessioninitialized) opts.onsessioninitialized('sid-close')
        }
        sessionId = 'sid-close'
        onclose?: () => void
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(capturedTransport.onclose).to.be.a('function')
      // Calling onclose should not throw (session is in transports map)
      expect(() => capturedTransport.onclose()).to.not.throw()
    })
  })

  // =========================================================================
  // Session lifecycle
  // =========================================================================
  describe('session lifecycle', () => {
    it('should reuse existing transport when mcp-session-id header matches stored session', async () => {
      const existingHandleRequest = sinon.stub().resolves()
      let storedSessionId: string | undefined

      sdkTransportExports.StreamableHTTPServerTransport = class {
        sessionId: string
        handleRequest = existingHandleRequest
        constructor(opts: any) {
          this.sessionId = 'reuse-session-id'
          storedSessionId = this.sessionId
          if (opts?.onsessioninitialized) opts.onsessioninitialized(this.sessionId)
        }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const handler = handleMCPRequest(appConfig)

      // First request: initialize → creates and stores the transport
      const req1 = createMockRequest({ headers: {}, body: initBody })
      const res1 = createMockResponse()
      await handler(req1, res1 as any, createMockNext())

      // Second request: reuse existing session via mcp-session-id header
      const req2 = createMockRequest({
        headers: { 'mcp-session-id': storedSessionId },
        body: { jsonrpc: '2.0', method: 'tools/list', id: 2 },
      })
      const res2 = createMockResponse()
      const next2 = createMockNext()
      await handler(req2, res2 as any, next2)

      // handleRequest was called for both requests
      expect(existingHandleRequest.callCount).to.equal(2)
      expect(next2.called).to.be.false
    })

    it('should return 400 when mcp-session-id is provided but session does not exist', async () => {
      const handler = handleMCPRequest(appConfig)
      const req = createMockRequest({
        headers: { 'mcp-session-id': 'non-existent-session-id' },
        body: initBody,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res as any, next)

      expect(res.status.calledWith(400)).to.be.true
      expect(next.called).to.be.false
    })

    it('should return 400 when no session ID and body is not an initialize request', async () => {
      const handler = handleMCPRequest(appConfig)
      const req = createMockRequest({
        headers: {},
        body: { jsonrpc: '2.0', method: 'tools/list', id: 1 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res as any, next)

      expect(res.status.calledWith(400)).to.be.true
      expect(next.called).to.be.false
    })

    it('should return 400 with jsonrpc error structure on missing session', async () => {
      const handler = handleMCPRequest(appConfig)
      const req = createMockRequest({ headers: {}, body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res as any, next)

      expect(res.status.calledWith(400)).to.be.true
      const jsonArg = res.json.firstCall?.args[0]
      expect(jsonArg).to.have.property('jsonrpc', '2.0')
      expect(jsonArg).to.have.nested.property('error.code', -32000)
    })

    it('should accept array body where one element has method initialize', async () => {
      const connectStub = sinon.stub().resolves()
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: connectStub },
      })

      const arrayBody = [{ jsonrpc: '2.0', method: 'initialize', id: 1 }]
      const req = createMockRequest({ headers: {}, body: arrayBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(connectStub.calledOnce).to.be.true
      expect(next.called).to.be.false
    })

    it('should pick first value when mcp-session-id header is an array', async () => {
      const handler = handleMCPRequest(appConfig)
      const req = createMockRequest({
        headers: { 'mcp-session-id': ['non-existent-id', 'other-id'] },
        body: initBody,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res as any, next)

      // non-existent-id is not in transports → 400
      expect(res.status.calledWith(400)).to.be.true
    })
  })

  // =========================================================================
  // createMCPServer arguments
  // =========================================================================
  describe('createMCPServer configuration', () => {
    it('should call createMCPServer with dynamic: false', async () => {
      const createStub = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })
      mcpServerExports.createMCPServer = createStub

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(createStub.calledOnce).to.be.true
      expect(createStub.firstCall.args[0].dynamic).to.be.false
    })

    it('should call createMCPServer with correct serverURL', async () => {
      const createStub = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })
      mcpServerExports.createMCPServer = createStub
      appConfig.oauthBackendUrl = 'https://prod.example.com'

      const req = createMockRequest({ headers: {}, body: initBody })
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

      const req = createMockRequest({ headers: {}, body: initBody })
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
        body: initBody,
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

      const req = createMockRequest({ headers: {}, body: initBody })
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

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.called).to.be.false
    })

    it('should complete the full flow: import → transport → server → connect → handleRequest', async () => {
      const callOrder: string[] = []

      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          callOrder.push('transport-created')
          if (opts?.onsessioninitialized) opts.onsessioninitialized('sid-flow')
        }
        sessionId = 'sid-flow'
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

      const req = createMockRequest({ headers: {}, body: initBody })
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

    it('should work with POST request sending an initialize body', async () => {
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({
        method: 'POST',
        headers: { authorization: 'Bearer tok' },
        body: initBody,
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.called).to.be.false
    })

    it('should work with GET request sending an initialize body', async () => {
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({
        method: 'GET',
        headers: { authorization: 'Bearer tok' },
        body: initBody,
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

      const req = createMockRequest({ headers: {}, body: initBody })
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

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should call next(error) when transport.handleRequest rejects', async () => {
      const error = new Error('handleRequest failed')
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          if (opts?.onsessioninitialized) opts.onsessioninitialized('sid-err')
        }
        sessionId = 'sid-err'
        handleRequest() { return Promise.reject(error) }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const req = createMockRequest({ headers: {}, body: initBody })
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

      const req = createMockRequest({ headers: {}, body: initBody })
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

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.equal(error)
    })

    it('should log the error and call next when an unexpected error occurs', async () => {
      const error = new Error('unexpected')
      mcpServerExports.createMCPServer = sinon.stub().throws(error)

      const req = createMockRequest({ headers: {}, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handleMCPRequest(appConfig)(req, res as any, next)

      expect(next.calledOnce).to.be.true
      expect(errorStub.calledOnce).to.be.true
      expect(errorStub.firstCall.args[0]).to.equal('MCP request failed')
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

      const req1 = createMockRequest({ headers: { authorization: 'Bearer token-a' }, body: initBody })
      const req2 = createMockRequest({ headers: { authorization: 'Bearer token-b' }, body: initBody })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req1, res as any, next)
      await handler(req2, res as any, next)

      expect(tokens).to.deep.equal(['token-a', 'token-b'])
    })

    it('should create a new transport for each initialize request', async () => {
      const createdTransports: any[] = []
      sdkTransportExports.StreamableHTTPServerTransport = class {
        constructor(opts: any) {
          createdTransports.push(this)
          if (opts?.onsessioninitialized) opts.onsessioninitialized(`sid-${createdTransports.length}`)
        }
        get sessionId() { return `sid-${createdTransports.indexOf(this) + 1}` }
        handleRequest() { return Promise.resolve() }
      }
      mcpServerExports.createMCPServer = sinon.stub().returns({
        server: { connect: sinon.stub().resolves() },
      })

      const handler = handleMCPRequest(appConfig)
      const res = createMockResponse()
      const next = createMockNext()

      await handler(createMockRequest({ headers: {}, body: initBody }), res as any, next)
      await handler(createMockRequest({ headers: {}, body: initBody }), res as any, next)

      expect(createdTransports).to.have.length(2)
      expect(createdTransports[0]).to.not.equal(createdTransports[1])
    })
  })
})
