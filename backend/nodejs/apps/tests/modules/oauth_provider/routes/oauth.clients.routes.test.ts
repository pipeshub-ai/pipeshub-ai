import 'reflect-metadata'
import express from 'express'
import type { Server } from 'http'
import { expect } from 'chai'
import sinon from 'sinon'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { createOAuthClientsRouter } from '../../../../src/modules/oauth_provider/routes/oauth.clients.routes'

describe('OAuth Clients Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createOAuthClientsRouter', () => {
    it('should be a function', () => {
      expect(createOAuthClientsRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthAppController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createOAuthClientsRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      // Should have routes for GET, POST, etc.
      expect(router.stack.length).to.be.greaterThan(0)
    })

    it('should mount requireSessionAuth router-wide, right after authenticate and before any route', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthAppController') return {}
          // Named so the bound layer shows up as 'bound authenticate' in router.stack.
          if (key === 'AuthMiddleware') return { authenticate: function authenticate() {} }
          return {}
        }),
      }

      const router = createOAuthClientsRouter(mockContainer as any)
      const names = router.stack.map((layer: any) => layer.name)
      const authIdx = names.indexOf('bound authenticate')
      const sessionIdx = names.indexOf('requireSessionAuth')
      const firstRouteIdx = router.stack.findIndex((layer: any) => !!layer.route)
      expect(authIdx).to.be.greaterThan(-1)
      expect(sessionIdx).to.equal(authIdx + 1)
      expect(firstRouteIdx).to.be.greaterThan(sessionIdx)
    })
  })

  // Exercises the real middleware chain over HTTP: the only stubs are the
  // authenticate step (which decides what kind of principal req.user is) and
  // the controller (which records whether the request got through).
  describe('token-type enforcement (GHSA-5f37-vxfm-885c)', () => {
    let app: express.Express
    let server: Server | undefined
    let principal: Record<string, any> | undefined
    let controller: { listScopes: sinon.SinonStub; createApp: sinon.SinonStub }

    beforeEach(() => {
      principal = undefined
      // refuseServiceAccountCaller looks the caller up in Mongo; answer as a
      // person so the session-positive cases reach the controller.
      sinon.stub(Users, 'findOne').returns({
        select: () => ({ lean: () => ({ exec: async () => ({ kind: 'individual' }) }) }),
      } as any)
      controller = {
        listScopes: sinon.stub().callsFake((_req: any, res: any) => res.status(200).json({ scopes: [] })),
        createApp: sinon.stub().callsFake((_req: any, res: any) => res.status(201).json({ app: {} })),
      }
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 1000 }
          if (key === 'OAuthAppController') return controller
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
      app.use('/api/v1/oauth-clients', createOAuthClientsRouter(mockContainer as any))
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

    const validCreateBody = {
      name: 'Evil App',
      allowedScopes: ['org:admin'],
      redirectUris: ['https://attacker.example/cb'],
    }

    it('lets a session-authenticated user through to the controller', async () => {
      principal = { userId: 'u1', orgId: 'o1', role: 'member' }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/oauth-clients/scopes`)
      expect(res.status).to.equal(200)
      expect(controller.listScopes.calledOnce).to.be.true
    })

    it('rejects an OAuth access token with 403 before reaching the controller', async () => {
      principal = {
        userId: 'u1', orgId: 'o1', role: 'member',
        isOAuth: true, oauthClientId: 'client-1', oauthScopes: ['org:read'],
      }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/oauth-clients`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(validCreateBody),
      })
      expect(res.status).to.equal(403)
      expect(controller.createApp.called).to.be.false
    })

    it('rejects a personal access token with 403 (PATs are OAuth access tokens)', async () => {
      principal = {
        userId: 'u1', orgId: 'o1', role: 'admin',
        isOAuth: true, oauthClientId: 'pat-system:o1', oauthScopes: ['org:read'],
      }
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/oauth-clients/scopes`)
      expect(res.status).to.equal(403)
      expect(controller.listScopes.called).to.be.false
    })

    it('still returns 401 when no credentials are presented', async () => {
      const port = await listen()

      const res = await fetch(`http://127.0.0.1:${port}/api/v1/oauth-clients/scopes`)
      expect(res.status).to.equal(401)
      expect(controller.listScopes.called).to.be.false
    })
  })
})
