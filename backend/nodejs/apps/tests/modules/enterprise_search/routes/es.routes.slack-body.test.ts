import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import type { NextFunction, Request, RequestHandler, Response, Router } from 'express'
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../../src/modules/enterprise_search/routes/es.routes'
import { ValidationMiddleware } from '../../../../src/libs/middlewares/validation.middleware'
import { slackReply } from '../../../../src/modules/agent-harness/transport/chat-turn-route'
import { CONVERSATION_TYPES } from '../../../../src/modules/enterprise_search/services/conversation/conversation.types'
import { markingGuards } from '../helpers/guarded-chat'
import { turnDeps } from '../helpers/turn-deps'

/**
 * The Slack bot's turn on its four stream routes. The body is the one the bot posts
 * (`integrations/slack-bot/src/index.ts`), with each field main's bot sends. On main the agent routes did not validate
 * it, so every field reached the turn as sent. They validate it now, and the validation must keep each field: the
 * thread most of all, since the agent harness posts the run's end there when the bot's stream ended first.
 */

const THREAD = { channel: 'C0123456789', threadTs: '1792183630.868055' }
const CONVERSATION = '65f1c0a1b2c3d4e5f6a7b8c9'

/** What the bot sends: an agent's turn in quick mode, or the assistant's Agent mode on the agent harness. */
function slackBody(agent: boolean): Record<string, unknown> {
  return {
    query: 'What changed in the launch plan?',
    chatMode: agent ? 'quick' : 'agent',
    ...(agent ? {} : { harness: 'durable' }),
    currentTime: '2026-11-28T05:00:00.000Z',
    timezone: 'Asia/Kolkata',
    callerDisplayName: 'Ana Ruiz',
    callerEmail: 'ana@example.com',
    slackThread: THREAD,
    attachments: [
      {
        recordId: 'rec-1',
        recordName: 'plan.pdf',
        mimeType: 'application/pdf',
        extension: 'pdf',
        virtualRecordId: 'vr-1',
      },
    ],
  }
}

type RouterName = 'agent' | 'assistant'

const ROUTES: ReadonlyArray<{ router: RouterName; path: string; params: Record<string, string> }> = [
  { router: 'agent', path: '/:agentKey/conversations/internal/stream', params: { agentKey: 'agent-1' } },
  {
    router: 'agent',
    path: '/:agentKey/conversations/internal/:conversationId/messages/stream',
    params: { agentKey: 'agent-1', conversationId: CONVERSATION },
  },
  { router: 'assistant', path: '/internal/stream', params: {} },
  {
    router: 'assistant',
    path: '/internal/:conversationId/messages/stream',
    params: { conversationId: CONVERSATION },
  },
]

/** Marks the handlers `ValidationMiddleware.validate` builds, so a route's own validation can be found in its stack. */
const VALIDATION = Symbol('route validation')

interface RouteLayer {
  route?: {
    path: string
    methods: Record<string, boolean>
    stack: Array<{ handle: RequestHandler & { [VALIDATION]?: true } }>
  }
}

const pass = (_req: Request, _res: Response, next: NextFunction): void => next()

function buildRouters(): Record<RouterName, Router> {
  const validate = ValidationMiddleware.validate.bind(ValidationMiddleware)
  sinon
    .stub(ValidationMiddleware, 'validate')
    .callsFake((schema, options) => Object.assign(validate(schema, options), { [VALIDATION]: true as const }))
  const container = new Container()
  container.bind('AuthMiddleware').toConstantValue({ authenticate: pass, scopedTokenValidator: () => pass })
  container.bind('AppConfig').toConstantValue({
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-secret',
    cmBackend: 'http://localhost:3001',
    iamBackend: 'http://localhost:3001',
  })
  container.bind(CONVERSATION_TYPES.ConversationGuards).toConstantValue(markingGuards())
  container.bind(CONVERSATION_TYPES.ConversationTurnDeps).toConstantValue(turnDeps())
  return {
    agent: createAgentConversationalRouter(container),
    assistant: createConversationalRouter(container),
  }
}

/** The validation the router runs on `POST path`, as it is wired there. */
function validationOf(router: Router, path: string): RequestHandler {
  const layer = (router as unknown as { stack: RouteLayer[] }).stack.find(
    (l) => l.route?.path === path && l.route.methods['post'] === true,
  )
  expect(layer, `POST ${path} exists`).to.not.equal(undefined)
  const validations = layer!.route!.stack.map((s) => s.handle).filter((handle) => handle[VALIDATION] === true)
  expect(validations, `POST ${path} validates the request once`).to.have.length(1)
  return validations[0]!
}

/** The request as the route's later middleware sees it, after its validation. */
async function validated(
  route: (typeof ROUTES)[number],
  body: Record<string, unknown>,
): Promise<Request> {
  const handler = validationOf(buildRouters()[route.router], route.path)
  const req = {
    body: structuredClone(body),
    params: { ...route.params },
    query: {},
    headers: {},
    // The scoped token the bot mints names its configuration: the bot the run's end is posted with.
    tokenPayload: { configId: 'bot-1' },
  } as unknown as Request
  const next = sinon.stub()
  await handler(req, {} as Response, next as unknown as NextFunction)
  expect(next.calledOnce, 'the validation passes the request on').to.equal(true)
  expect(next.firstCall.args, 'the validation refuses nothing').to.deep.equal([])
  return req
}

describe("the Slack bot's turn on its internal stream routes", () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const route of ROUTES) {
    const agent = route.router === 'agent'
    describe(`POST ${route.path} (${route.router})`, () => {
      it('keeps every field the bot sends, each as sent', async () => {
        const req = await validated(route, slackBody(agent))
        expect(req.body).to.deep.equal(slackBody(agent))
      })

      it("gives the run the thread the turn came from, where the run's end is posted", async () => {
        const req = await validated(route, slackBody(agent))
        expect(slackReply(req, req.body)).to.deep.equal({ slack: { botId: 'bot-1', ...THREAD } })
      })
    })
  }
})
