import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Container } from 'inversify'
import { createAgentConversationalRouter } from '../../../../src/modules/enterprise_search/routes/es.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'
import { slackJwtGenerator } from '../../../../src/libs/utils/createJwt'
import { createMockLogger } from '../../../helpers/mock-logger'

const JWT_SECRET = 'test-jwt-secret'
const SCOPED_SECRET = 'test-scoped-secret'

// The Slack bot's agent entry points. They take a scoped token, not a user
// session or OAuth token, so the scope on that token is their only gate.
const INTERNAL_AGENT_ROUTES = [
  '/:agentKey/conversations/internal/:conversationId/messages/stream',
  '/:agentKey/conversations/internal/stream',
  '/:agentKey/conversations/internal/attachments/upload',
]

function buildRouter() {
  const container = new Container()
  const authMiddleware = new AuthMiddleware(
    createMockLogger() as any,
    new AuthTokenService(JWT_SECRET, SCOPED_SECRET),
  )
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(authMiddleware)
  container.bind('AppConfig').toConstantValue({
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    jwtSecret: JWT_SECRET,
    scopedJwtSecret: SCOPED_SECRET,
  } as any)
  return createAgentConversationalRouter(container)
}

function firstHandler(router: any, path: string) {
  const layer = router.stack.find(
    (l: any) => l.route && l.route.path === path && l.route.methods.post,
  )
  expect(layer, `route ${path} must exist`).to.exist
  return layer.route.stack[0].handle
}

async function runGate(router: any, path: string, token: string | undefined) {
  const req: any = {
    headers: token ? { authorization: `Bearer ${token}` } : {},
    params: {},
    body: {},
    query: {},
  }
  const next = sinon.stub()
  await firstHandler(router, path)(req, {}, next)
  expect(next.calledOnce).to.be.true
  return { err: next.firstCall.args[0], req }
}

describe('T22 internal agent routes (Slack bot) require the scoped-token scope', () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const path of INTERNAL_AGENT_ROUTES) {
    describe(`POST ${path}`, () => {
      it('rejects a request with no token', async () => {
        const { err } = await runGate(buildRouter(), path, undefined)
        expect(err).to.exist
        expect(err.statusCode).to.equal(401)
      })

      it('rejects a scoped token that lacks conversation:create', async () => {
        const token = jwt.sign(
          { email: 'a@example.com', scopes: [TokenScopes.FETCH_CONFIG] },
          SCOPED_SECRET,
        )
        const { err } = await runGate(buildRouter(), path, token)
        expect(err).to.exist
        expect(err.statusCode).to.equal(401)
      })

      it('rejects a scoped token with no scopes claim', async () => {
        const token = jwt.sign({ email: 'a@example.com' }, SCOPED_SECRET)
        const { err } = await runGate(buildRouter(), path, token)
        expect(err).to.exist
        expect(err.statusCode).to.equal(401)
      })

      it('rejects a user session JWT, even one claiming the scope', async () => {
        const token = jwt.sign(
          {
            userId: 'u1',
            orgId: 'o1',
            role: 'admin',
            scopes: [TokenScopes.CONVERSATION_CREATE],
          },
          JWT_SECRET,
        )
        const { err } = await runGate(buildRouter(), path, token)
        expect(err).to.exist
        expect(err.statusCode).to.equal(401)
      })

      it('admits the token the Slack bot mints', async () => {
        const token = slackJwtGenerator('a@example.com', SCOPED_SECRET)
        const { err, req } = await runGate(buildRouter(), path, token)
        expect(err).to.be.undefined
        expect(req.tokenPayload.email).to.equal('a@example.com')
      })
    })
  }
})
