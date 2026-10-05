import 'reflect-metadata'
import express from 'express'
import type { Server } from 'http'
import { expect } from 'chai'
import sinon from 'sinon'
import { createPatRouter } from '../../../../src/modules/oauth_provider/routes/pat.routes'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'

describe('Personal Access Token Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createPatRouter', () => {
    it('should be a function', () => {
      expect(createPatRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'PatController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createPatRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      // GET /, POST /, GET /scopes, DELETE /:tokenId, GET /admin, DELETE /admin/:tokenId
      expect(router.stack.length).to.be.greaterThan(0)
    })

    it('should require an interactive session on every route, right after authenticate', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'PatController') return {}
          // Named so the bound layer shows up as 'bound authenticate'.
          if (key === 'AuthMiddleware') return { authenticate: function authenticate() {} }
          return {}
        }),
      }

      const router = createPatRouter(mockContainer as any)
      // Router-level middleware appears in the stack as layers without a route.
      const names = router.stack.filter((l: any) => !l.route).map((l: any) => l.name)
      const authIdx = names.indexOf('bound authenticate')
      expect(authIdx).to.be.greaterThan(-1)
      expect(names[authIdx + 1]).to.equal('requireSessionAuth')
    })

    it('should register the admin routes behind userAdminCheck', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'PatController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createPatRouter(mockContainer as any)
      const adminLayers = router.stack.filter(
        (layer: any) => layer.route?.path === '/admin' || layer.route?.path === '/admin/:tokenId',
      )
      expect(adminLayers).to.have.lengthOf(2)
      // Each admin route's middleware chain must include userAdminCheck (by
      // name), not just authentication — regular org members must not be
      // able to list or revoke other users' tokens.
      for (const layer of adminLayers) {
        const handlerNames = layer.route!.stack.map((s: any) => s.name)
        expect(handlerNames).to.include('userAdminCheck')
      }
    })
  })

  // Exercises the real middleware chain over HTTP. Only authenticate (decides
  // what req.user looks like), the service-account lookup, and the controller
  // (records whether the request got through) are stubbed.
  describe('token-type enforcement', () => {
    let app: express.Express
    let server: Server | undefined
    let principal: Record<string, any> | undefined
    let controller: { createToken: sinon.SinonStub; listTokens: sinon.SinonStub }

    beforeEach(() => {
      principal = undefined
      controller = {
        createToken: sinon.stub().callsFake((_req: any, res: any) =>
          res.status(201).json({ accessToken: 'phpat_minted' }),
        ),
        listTokens: sinon.stub().callsFake((_req: any, res: any) =>
          res.status(200).json({ tokens: [] }),
        ),
      }
      sinon.stub(Users, 'findOne').returns({
        select: () => ({ lean: () => ({ exec: async () => ({ kind: 'individual' }) }) }),
      } as any)
      const container = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 1000 }
          if (key === 'PatController') return controller
          if (key === 'AuthMiddleware') {
            return {
              authenticate: (req: any, _res: any, next: any) => {
                if (!principal) {
                  return next(Object.assign(new Error('No token provided'), { statusCode: 401 }))
                }
                req.user = principal
                next()
              },
            }
          }
          return {}
        }),
      }
      app = express()
      app.use(express.json())
      app.use('/api/v1/personal-access-tokens', createPatRouter(container as any))
      app.use((err: any, _req: any, res: any, _next: any) => {
        res.status(err.statusCode || 500).json({ message: err.message })
      })
    })

    afterEach(async () => {
      if (server) {
        await new Promise<void>((resolve, reject) => {
          server!.close((err) => (err ? reject(err) : resolve()))
        })
        server = undefined
      }
    })

    async function listen(): Promise<number> {
      return new Promise((resolve, reject) => {
        server = app.listen(0, () => {
          const addr = server!.address()
          if (addr && typeof addr === 'object') resolve(addr.port)
          else reject(new Error('no port'))
        })
      })
    }

    const createBody = { name: 'ci', expiryDays: 'never' }

    it('lets a session-authenticated member mint a token', async () => {
      principal = { userId: 'u1', orgId: 'o1', role: 'member' }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/personal-access-tokens`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(createBody),
      })
      expect(res.status).to.equal(201)
      expect(controller.createToken.calledOnce).to.be.true
    })

    // createToken caps scopes at the instance's MCP set, not at the caller's
    // own token, so a narrow PAT minting another PAT is a scope escalation.
    it('rejects minting driven by a personal access token with 403, never reaching the controller', async () => {
      principal = {
        userId: 'u1', orgId: 'o1', role: 'member',
        isOAuth: true, oauthClientId: 'pat-system:o1', oauthScopes: ['org:read'],
      }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/personal-access-tokens`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(createBody),
      })
      expect(res.status).to.equal(403)
      expect(controller.createToken.called).to.be.false
    })

    it('rejects minting driven by an OAuth access token with 403', async () => {
      principal = {
        userId: 'u1', orgId: 'o1', role: 'admin',
        isOAuth: true, oauthClientId: 'third-party-client', oauthScopes: ['org:admin'],
      }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/personal-access-tokens`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(createBody),
      })
      expect(res.status).to.equal(403)
      expect(controller.createToken.called).to.be.false
    })

    it('rejects listing driven by an OAuth access token with 403', async () => {
      principal = {
        userId: 'u1', orgId: 'o1', role: 'member',
        isOAuth: true, oauthClientId: 'third-party-client', oauthScopes: ['org:read'],
      }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/personal-access-tokens`)
      expect(res.status).to.equal(403)
      expect(controller.listTokens.called).to.be.false
    })
  })
})
