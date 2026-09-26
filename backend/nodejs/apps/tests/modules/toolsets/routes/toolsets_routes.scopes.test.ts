import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import { AddressInfo } from 'net'
import http from 'http'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes'

// Every toolset route and the one OAuth scope it demands. A route missing from
// this table fails the inventory test, so a new route cannot ship unscoped.
const EXPECTED_ROUTE_SCOPES: Record<string, string> = {
  'GET /registry': 'toolset:read',
  'GET /registry/:toolsetType/schema': 'toolset:read',
  'POST /': 'toolset:write',
  'GET /configured': 'toolset:read',
  'GET /:toolsetId/status': 'toolset:read',
  'GET /:toolsetId/config': 'toolset:read',
  'POST /:toolsetId/config': 'toolset:write',
  'PUT /:toolsetId/config': 'toolset:write',
  'DELETE /:toolsetId/config': 'toolset:delete',
  'POST /:toolsetId/reauthenticate': 'toolset:write',
  'GET /:toolsetId/oauth/authorize': 'toolset:read',
  'GET /oauth/callback': 'toolset:write',
  'GET /my-toolsets': 'toolset:read',
  'GET /instances': 'toolset:read',
  'POST /instances': 'toolset:write',
  'GET /instances/:instanceId': 'toolset:read',
  'PUT /instances/:instanceId': 'toolset:write',
  'DELETE /instances/:instanceId': 'toolset:delete',
  'POST /instances/:instanceId/authenticate': 'toolset:write',
  'PUT /instances/:instanceId/credentials': 'toolset:write',
  'DELETE /instances/:instanceId/credentials': 'toolset:delete',
  'POST /instances/:instanceId/reauthenticate': 'toolset:write',
  'GET /instances/:instanceId/oauth/authorize': 'toolset:read',
  'GET /instances/:instanceId/status': 'toolset:read',
  'GET /oauth-configs/:toolsetType': 'toolset:read',
  'PUT /oauth-configs/:toolsetType/:oauthConfigId': 'toolset:write',
  'DELETE /oauth-configs/:toolsetType/:oauthConfigId': 'toolset:delete',
  'GET /agents/:agentKey': 'agent:read',
  'POST /agents/:agentKey/instances/:instanceId/authenticate': 'agent:write',
  'PUT /agents/:agentKey/instances/:instanceId/credentials': 'agent:write',
  'DELETE /agents/:agentKey/instances/:instanceId/credentials': 'agent:write',
  'POST /agents/:agentKey/instances/:instanceId/reauthenticate': 'agent:write',
  'GET /agents/:agentKey/instances/:instanceId/oauth/authorize': 'agent:write',
}

const ALL_SCOPES = [
  'toolset:read',
  'toolset:write',
  'toolset:delete',
  'agent:read',
  'agent:write',
  'agent:execute',
  'conversation:chat',
  'mcp:read',
  'mcp:write',
  'mcp:delete',
]

function makeContainer() {
  const mockAuthMiddleware = {
    authenticate: (_req: any, _res: any, next: any) => next(),
  }
  const container: any = {
    get: sinon.stub().callsFake((key: string) => {
      if (key === 'AppConfig') return { connectorBackend: 'http://localhost:8088' }
      if (key === 'AuthMiddleware') return mockAuthMiddleware
      return undefined
    }),
    isBound: sinon.stub().returns(false),
  }
  return container
}

interface RouteInfo {
  key: string
  handlers: any[]
}

function listRoutes(router: any): RouteInfo[] {
  const routes: RouteInfo[] = []
  for (const layer of router.stack) {
    if (!layer.route) continue
    for (const method of Object.keys(layer.route.methods)) {
      routes.push({
        key: `${method.toUpperCase()} ${layer.route.path}`,
        handlers: layer.route.stack,
      })
    }
  }
  return routes
}

function runScopeMiddleware(route: RouteInfo, user: Record<string, unknown>): any {
  // handlers[0] = authenticate, handlers[1] = requireScopes(...)
  const next = sinon.stub()
  route.handlers[1].handle({ user, headers: {}, params: {}, query: {}, body: {} }, {}, next)
  expect(next.calledOnce, `${route.key}: scope middleware must call next exactly once`).to.be.true
  return next.firstCall.args[0]
}

describe('toolsets routes - OAuth scope enforcement', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('T18 route inventory', () => {
    it('every registered toolset route is listed with an expected scope', () => {
      const routes = listRoutes(createToolsetsRouter(makeContainer()))
      const registered = routes.map((r) => r.key).sort()
      expect(registered).to.deep.equal(Object.keys(EXPECTED_ROUTE_SCOPES).sort())
    })

    it('every toolset route rejects an OAuth token that holds no scopes', () => {
      for (const route of listRoutes(createToolsetsRouter(makeContainer()))) {
        const err = runScopeMiddleware(route, {
          userId: 'u1',
          orgId: 'o1',
          isOAuth: true,
          oauthScopes: [],
        })
        expect(err, `${route.key} must be scope-gated`).to.exist
        expect(err.statusCode, route.key).to.equal(403)
        expect(err.message).to.include('Insufficient scope')
      }
    })

    it('every toolset route admits a token holding exactly its expected scope', () => {
      for (const route of listRoutes(createToolsetsRouter(makeContainer()))) {
        const scope = EXPECTED_ROUTE_SCOPES[route.key]!
        const err = runScopeMiddleware(route, {
          userId: 'u1',
          orgId: 'o1',
          isOAuth: true,
          oauthScopes: [scope],
        })
        expect(err, `${route.key} should accept ${scope}`).to.be.undefined
      }
    })

    it('no toolset route accepts a scope other than its expected one', () => {
      for (const route of listRoutes(createToolsetsRouter(makeContainer()))) {
        const expected = EXPECTED_ROUTE_SCOPES[route.key]!
        const others = ALL_SCOPES.filter((s) => s !== expected)
        const err = runScopeMiddleware(route, {
          userId: 'u1',
          orgId: 'o1',
          isOAuth: true,
          oauthScopes: others,
        })
        expect(err, `${route.key} must require ${expected}`).to.exist
        expect(err.statusCode).to.equal(403)
      }
    })

    it('read-only toolset scope does not open any write or delete route', () => {
      for (const route of listRoutes(createToolsetsRouter(makeContainer()))) {
        const expected = EXPECTED_ROUTE_SCOPES[route.key]
        if (expected === 'toolset:read' || expected === 'agent:read') continue
        const err = runScopeMiddleware(route, {
          userId: 'u1',
          orgId: 'o1',
          isOAuth: true,
          oauthScopes: ['toolset:read', 'agent:read'],
        })
        expect(err, `${route.key} must not be reachable with read scopes`).to.exist
      }
    })
  })

  describe('T17 session JWT (web UI)', () => {
    it('every toolset route lets a session JWT through the scope check', () => {
      for (const route of listRoutes(createToolsetsRouter(makeContainer()))) {
        const err = runScopeMiddleware(route, {
          userId: 'u1',
          orgId: 'o1',
          role: 'member',
        })
        expect(err, `${route.key} must not scope-check a session`).to.be.undefined
      }
    })
  })

  describe('over HTTP', () => {
    let server: http.Server
    let baseUrl: string
    let executeStub: sinon.SinonStub

    beforeEach(async () => {
      executeStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({ statusCode: 201, data: { status: 'success' } } as any)

      const app = express()
      app.use(express.json())
      // Stands in for AuthMiddleware.authenticate: the test says who the caller is.
      app.use((req: any, _res, next) => {
        const scopes = req.headers['x-test-scopes']
        req.user =
          scopes === undefined
            ? { userId: 'u1', orgId: 'o1', role: 'admin' }
            : {
                userId: 'u1',
                orgId: 'o1',
                isOAuth: true,
                oauthScopes: String(scopes).split(',').filter(Boolean),
              }
        next()
      })
      app.use('/api/v1/toolsets', createToolsetsRouter(makeContainer()))
      app.use((err: any, _req: any, res: any, _next: any) => {
        res.status(err.statusCode ?? 500).json({ message: err.message })
      })
      server = app.listen(0)
      await new Promise((resolve) => server.once('listening', resolve))
      baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/toolsets`
    })

    afterEach(async () => {
      await new Promise((resolve) => server.close(resolve))
    })

    const createInstanceBody = JSON.stringify({
      instanceName: 'jira',
      toolsetType: 'jira',
      authType: 'API_TOKEN',
    })

    async function call(
      method: string,
      path: string,
      scopes: string[] | undefined,
      body?: string,
    ) {
      const headers: Record<string, string> = { 'content-type': 'application/json' }
      if (scopes !== undefined) headers['x-test-scopes'] = scopes.join(',')
      return fetch(`${baseUrl}${path}`, { method, headers, body })
    }

    it('T15 a chat-only PAT cannot create a toolset instance', async () => {
      const res = await call('POST', '/instances', ['conversation:chat'], createInstanceBody)
      expect(res.status).to.equal(403)
      expect(executeStub.called).to.be.false
    })

    it('T15 a chat-only PAT cannot write toolset credentials', async () => {
      const res = await call(
        'PUT',
        '/instances/inst-1/credentials',
        ['conversation:chat'],
        JSON.stringify({ auth: { apiToken: 'x' } }),
      )
      expect(res.status).to.equal(403)
      expect(executeStub.called).to.be.false
    })

    it('T15 a chat-only PAT cannot write agent toolset credentials', async () => {
      const res = await call(
        'PUT',
        '/agents/agent-1/instances/inst-1/credentials',
        ['conversation:chat', 'agent:execute'],
        JSON.stringify({ auth: { apiToken: 'x' } }),
      )
      expect(res.status).to.equal(403)
      expect(executeStub.called).to.be.false
    })

    it('T15 a chat-only PAT cannot read toolset instances either', async () => {
      const res = await call('GET', '/instances', ['conversation:chat'])
      expect(res.status).to.equal(403)
      expect(executeStub.called).to.be.false
    })

    it('T16 a PAT holding toolset:write creates a toolset instance', async () => {
      const res = await call('POST', '/instances', ['toolset:write'], createInstanceBody)
      expect(res.status).to.equal(201)
      expect(executeStub.calledOnce).to.be.true
    })

    it('T16 a PAT holding toolset:delete removes credentials, toolset:write alone does not', async () => {
      const denied = await call('DELETE', '/instances/inst-1/credentials', ['toolset:write'])
      expect(denied.status).to.equal(403)

      executeStub.resolves({ statusCode: 200, data: { status: 'success' } } as any)
      const allowed = await call('DELETE', '/instances/inst-1/credentials', ['toolset:delete'])
      expect(allowed.status).to.equal(200)
    })

    it('T17 a session JWT creates a toolset instance with no OAuth scopes at all', async () => {
      const res = await call('POST', '/instances', undefined, createInstanceBody)
      expect(res.status).to.equal(201)
      expect(executeStub.calledOnce).to.be.true
    })
  })
})
