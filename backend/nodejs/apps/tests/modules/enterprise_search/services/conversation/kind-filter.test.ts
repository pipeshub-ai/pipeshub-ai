import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../../src/modules/enterprise_search/controller/es_controller'
import {
  conversationContextOf,
  kindFilterOf,
  setConversationContext,
} from '../../../../../src/modules/enterprise_search/services/conversation/http/conversation-context'
import { EXCLUDE_AGENT, ONLY_AGENT } from '../../../../../src/modules/enterprise_search/constants/constants'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from '../../controller/chat-test-harness'
import { withOwnerGrant } from '../../helpers/conversation-grant'
import { turnDeps } from '../../helpers/turn-deps'

/** A guard may authorize a conversation of another kind than the route's; the handlers then read that kind. */

const ORG = oid()
const OWNER = oid()
const AGENT_KEY = 'agent-1'
const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never

describe('kindFilterOf', () => {
  afterEach(() => sinon.restore())

  it('is the handler’s own filter when no guard ran, or the guard set none', () => {
    expect(kindFilterOf({}, EXCLUDE_AGENT)).to.equal(EXCLUDE_AGENT)
    const req = withOwnerGrant({ params: { conversationId: String(oid()) }, user: { userId: OWNER, orgId: ORG } })
    expect(kindFilterOf(req, ONLY_AGENT)).to.equal(ONLY_AGENT)
  })

  it('is the guard’s filter when it set one', () => {
    const req = withOwnerGrant({ params: { conversationId: String(oid()) }, user: { userId: OWNER, orgId: ORG } })
    setConversationContext(req as never, { ...conversationContextOf(req as never), kindFilter: { sessionType: 'agent' } })
    expect(kindFilterOf(req, EXCLUDE_AGENT)).to.deep.equal({ sessionType: 'agent' })
  })

  describe('the handlers that read their conversation by kind honour it', () => {
    /** A conversation stored under the other kind: the route's own filter cannot see it. */
    const world = (storedAs: 'chat' | 'agent') => {
      const store = new InMemoryChatStore()
      const ai = new FakeAIBackend()
      store.install()
      ai.install()
      const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: storedAs, agentKey: AGENT_KEY })
      store.addMessage(session, { messageType: 'user_query', content: 'q' })
      const bot = store.addMessage(session, { messageType: 'bot_response', content: 'a' })
      return { store, ai, session, botId: String(bot._id) }
    }
    const request = (w: ReturnType<typeof world>, agentRoute: boolean, kindFilter: object | undefined, extra: Record<string, unknown> = {}) => {
      const req = withOwnerGrant({
        headers: { authorization: 'Bearer token' },
        params: { conversationId: String(w.session._id), messageId: w.botId, ...(agentRoute && { agentKey: AGENT_KEY }) },
        query: {},
        body: {},
        user: { userId: OWNER, orgId: ORG },
        context: {},
        ...extra,
      })
      if (kindFilter) setConversationContext(req as never, { ...conversationContextOf(req as never), kindFilter })
      return req
    }
    const call = async (handler: (req: never, res: never, next: never) => unknown, req: unknown) => {
      const res = new FakeSSEResponse()
      const next = sinon.stub()
      await handler(req as never, res as never, next as never)
      await settle()
      return { res, next }
    }

    for (const [name, agentRoute, handler] of [
      ['getConversationById', false, controller.getConversationById(appConfig)],
      ['getAgentConversationById', true, controller.getAgentConversationById],
    ] as const) {
      it(`${name} opens the other kind only through the guard’s filter`, async () => {
        const w = world(agentRoute ? 'chat' : 'agent')
        const other = { sessionType: agentRoute ? 'chat' : 'agent' }
        const own = await call(handler as never, request(w, agentRoute, undefined))
        expect(own.next.firstCall?.args[0]?.statusCode, 'the route’s own kind').to.equal(404)
        const guarded = await call(handler as never, request(w, agentRoute, other))
        expect(guarded.next.called).to.equal(false)
        expect((guarded.res.jsonBody as { conversation: { id?: unknown; _id?: unknown } }).conversation).to.not.equal(undefined)
      })
    }

    it('cancelConversationStream finds the other kind only through the guard’s filter', async () => {
      const w = world('agent')
      w.ai.reply(/\/api\/v1\/chat\/cancel$/, 200, { cancelled: true })
      const own = await call(controller.cancelConversationStream(appConfig) as never, request(w, false, undefined, { body: { runId: 'run-1' } }))
      expect(own.next.firstCall?.args[0]?.statusCode).to.equal(404)
      const guarded = await call(controller.cancelConversationStream(appConfig) as never, request(w, false, { sessionType: 'agent' }, { body: { runId: 'run-1' } }))
      expect(guarded.next.called).to.equal(false)
      expect(guarded.res.jsonBody).to.deep.equal({ cancelled: true })
    })

    for (const [name, agentRoute, handler] of [
      ['regenerateAnswers', false, controller.regenerateAnswers(appConfig, turnDeps())],
      ['regenerateAgentAnswers', true, controller.regenerateAgentAnswers(appConfig, turnDeps())],
    ] as const) {
      it(`${name} regenerates in the other kind only through the guard’s filter`, async () => {
        const w = world(agentRoute ? 'chat' : 'agent')
        const other = { sessionType: agentRoute ? 'chat' : 'agent' }
        await call(handler as never, request(w, agentRoute, undefined))
        expect(w.ai.streamCalls, 'the route’s own kind finds nothing to regenerate').to.deep.equal([])
        const guarded = call(handler as never, request(w, agentRoute, other))
        await settle()
        expect(w.ai.streamCalls).to.have.length(1)
        w.ai.finish()
        await guarded
      })
    }
  })
})
