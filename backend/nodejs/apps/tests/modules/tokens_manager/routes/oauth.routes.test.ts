import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthRouter } from '../../../../src/modules/tokens_manager/routes/oauth.routes'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'

const buildRouter = () => {
  const container: any = {
    get: sinon.stub().callsFake((key: string) => {
      if (key === 'AppConfig') return { connectorBackend: 'http://localhost:8088' }
      if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
    }),
  }
  return createOAuthRouter(container)
}

// Runs the route's validation middleware and then its handler, skipping
// authentication and the scope check, and returns the body sent to Python.
const forwardedBody = async (method: string, path: string, req: any): Promise<any> => {
  const layer = (buildRouter() as any).stack.find(
    (r: any) => r.route?.path === path && r.route.methods[method],
  )
  const [validate, handler] = layer.route.stack.slice(-2).map((l: any) => l.handle)
  const forward = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: {} })
  sinon.stub(connectorUtils, 'handleConnectorResponse')
  const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() }
  const next = sinon.stub()
  await validate(req, res, next)
  expect(next.firstCall.args, 'validation rejected the request').to.deep.equal([])
  next.resetHistory()
  await handler(req, res, next)
  expect(next.called, `handler failed: ${next.firstCall?.args[0]?.message}`).to.be.false
  return forward.firstCall.args[3]
}

describe('tokens_manager/routes/oauth.routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createOAuthRouter', () => {
    it('should create a router with expected routes', () => {
      const mockAuthMiddleware = { authenticate: sinon.stub() }
      const mockAppConfig = { connectorBackend: 'http://localhost:8088' }

      const container: any = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'AppConfig') return mockAppConfig
          if (key === 'AuthMiddleware') return mockAuthMiddleware
        }),
      }

      const router = createOAuthRouter(container)

      expect(router).to.exist
      const routes = (router as any).stack.filter((r: any) => r.route)
      expect(routes.length).to.be.greaterThan(0)

      // Verify key routes exist
      const paths = routes.map((r: any) => ({
        path: r.route.path,
        methods: Object.keys(r.route.methods),
      }))

      // Should have GET /registry
      expect(paths.some((p: any) => p.path === '/registry' && p.methods.includes('get'))).to.be.true
      // Should have GET /
      expect(paths.some((p: any) => p.path === '/' && p.methods.includes('get'))).to.be.true
      // Should have POST /:connectorType
      expect(paths.some((p: any) => p.path === '/:connectorType' && p.methods.includes('post'))).to.be.true
      // Should have DELETE /:connectorType/:configId
      expect(paths.some((p: any) => p.path === '/:connectorType/:configId' && p.methods.includes('delete'))).to.be.true
    })
  })

  describe('baseUrl', () => {
    const user = { userId: 'user-1', orgId: 'org-1' }

    it('reaches the connector service when a config is created', async () => {
      const body = await forwardedBody('post', '/:connectorType', {
        user,
        params: { connectorType: 'GOOGLE_DRIVE' },
        query: {},
        headers: {},
        body: { oauthInstanceName: 'prod', config: { clientId: 'x' }, baseUrl: 'https://pipeshub.example.com' },
      })
      expect(body).to.deep.equal({
        oauthInstanceName: 'prod',
        config: { clientId: 'x' },
        baseUrl: 'https://pipeshub.example.com',
      })
    })

    it('reaches the connector service when a config is updated', async () => {
      const body = await forwardedBody('put', '/:connectorType/:configId', {
        user,
        params: { connectorType: 'GOOGLE_DRIVE', configId: 'cfg-1' },
        query: {},
        headers: {},
        body: { config: { clientId: 'x' }, baseUrl: 'https://pipeshub.example.com' },
      })
      expect(body).to.deep.equal({ config: { clientId: 'x' }, baseUrl: 'https://pipeshub.example.com' })
    })

    it('may be left out, as the API reference says', async () => {
      const body = await forwardedBody('post', '/:connectorType', {
        user,
        params: { connectorType: 'GOOGLE_DRIVE' },
        query: {},
        headers: {},
        body: { oauthInstanceName: 'prod', config: { clientId: 'x' } },
      })
      expect(body).to.deep.equal({ oauthInstanceName: 'prod', config: { clientId: 'x' } })
    })
  })
})
