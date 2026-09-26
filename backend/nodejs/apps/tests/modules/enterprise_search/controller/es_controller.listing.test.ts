import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor'
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const appConfig = {
  aiBackend: 'http://ai.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OWNER = oid()
const OTHER = oid()
const AGENT_KEY = 'agent-1'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

interface Outcome<T> {
  status: number | undefined
  body: T
  error: (Error & { statusCode?: number }) | undefined
}

async function call<T = Record<string, unknown>>(
  handler: JsonHandler,
  { params = {}, query = {}, body = {}, user = { userId: OWNER, orgId: ORG } }: {
    params?: Record<string, string>
    query?: Record<string, string>
    body?: Record<string, unknown>
    user?: { userId: Types.ObjectId; orgId: Types.ObjectId }
  } = {},
): Promise<Outcome<T>> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: { authorization: 'Bearer token' }, params, query, body, user, context: { requestId: 'req-list' } }
  await handler(req as never, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody as T, error: next.firstCall?.args[0] as Outcome<T>['error'] }
}

function fresh(): { store: InMemoryChatStore; ai: FakeAIBackend } {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  return { store, ai }
}

describe('es_controller listing and paging', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('older messages of one conversation, newest page first', () => {
    const cases = [
      { name: 'getConversationById', handler: controller.getConversationById(appConfig) as JsonHandler, agent: false },
      { name: 'getAgentConversationById', handler: controller.getAgentConversationById as JsonHandler, agent: true },
    ]

    for (const c of cases) {
      it(`${c.name}: every message appears on exactly one page and a page past the start is empty`, async () => {
        const { store } = fresh()
        const session = store.addSession({
          orgId: ORG,
          userId: OWNER,
          initiator: OWNER,
          sessionType: c.agent ? 'agent' : 'chat',
          ...(c.agent ? { agentKey: AGENT_KEY } : {}),
        })
        for (let i = 1; i <= 5; i += 1) {
          store.addMessage(session, { messageType: i % 2 ? 'user_query' : 'bot_response', content: `m${String(i)}` })
        }
        const params = { conversationId: String(session._id), agentKey: AGENT_KEY }

        const pages: string[][] = []
        const olderFlags: boolean[] = []
        for (const page of ['1', '2', '3', '4']) {
          const out = await call<{ conversation: { messages: Array<{ content: string }>; pagination: { hasNextPage: boolean } } }>(
            c.handler,
            { params, query: { page, limit: '2', sortOrder: 'asc' } },
          )
          expect(out.error, `page ${page}`).to.equal(undefined)
          pages.push(out.body.conversation.messages.map((m) => m.content).sort())
          olderFlags.push(out.body.conversation.pagination.hasNextPage)
        }

        expect(pages).to.deep.equal([['m4', 'm5'], ['m2', 'm3'], ['m1'], []])
        expect(olderFlags).to.deep.equal([true, true, false, false])
      })
    }
  })

  describe('getAllConversations', () => {
    it('lists only the caller’s own chats, newest activity first, one page at a time', async () => {
      const { store } = fresh()
      for (let i = 1; i <= 3; i += 1) {
        store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: `mine ${String(i)}`, lastActivityAt: i })
      }
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, title: 'someone else’s' })
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'agent chat', sessionType: 'agent', agentKey: AGENT_KEY })
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'archived', isArchived: true })

      const first = await call<{ conversations: Array<{ title: string }>; pagination: Record<string, unknown> }>(
        controller.getAllConversations as JsonHandler,
        { query: { page: '1', limit: '2' } },
      )
      const second = await call<{ conversations: Array<{ title: string }> }>(controller.getAllConversations as JsonHandler, {
        query: { page: '2', limit: '2' },
      })

      expect(first.body.conversations.map((c) => c.title)).to.deep.equal(['mine 3', 'mine 2'])
      expect(second.body.conversations.map((c) => c.title)).to.deep.equal(['mine 1'])
      expect(first.body.pagination).to.include({ totalCount: 3, totalPages: 2, hasNextPage: true, hasPrevPage: false })
    })

    it('source=shared lists chats others shared with the caller, and nothing else', async () => {
      const { store } = fresh()
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, title: 'shared with me', isShared: true, sharedWith: [{ userId: OWNER, accessLevel: 'read' }] })
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, title: 'shared with someone else', isShared: true, sharedWith: [{ userId: oid(), accessLevel: 'read' }] })
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'mine' })

      const out = await call<{ conversations: Array<{ title: string }> }>(controller.getAllConversations as JsonHandler, {
        query: { source: 'shared' },
      })

      expect(out.body.conversations.map((c) => c.title)).to.deep.equal(['shared with me'])
    })

    it('rejects an unknown source instead of guessing', async () => {
      fresh()
      const out = await call(controller.getAllConversations as JsonHandler, { query: { source: 'everyone' } })
      expect(out.error?.statusCode).to.equal(400)
    })
  })

  describe('listAllArchivesAgentConversation', () => {
    it('pages through the caller’s archived chats with one agent, newest first', async () => {
      const { store } = fresh()
      for (let i = 1; i <= 3; i += 1) {
        store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: 'agent', agentKey: AGENT_KEY, title: `archived ${String(i)}`, isArchived: true, archivedBy: OWNER, lastActivityAt: i })
      }
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: 'agent', agentKey: AGENT_KEY, title: 'active', lastActivityAt: 9 })
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: 'agent', agentKey: 'agent-2', title: 'other agent', isArchived: true, archivedBy: OWNER })
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, sessionType: 'agent', agentKey: AGENT_KEY, title: 'not mine', isArchived: true, archivedBy: OTHER })

      const handler = controller.listAllArchivesAgentConversation() as JsonHandler
      const page1 = await call<{ conversations: Array<{ title: string }>; pagination: Record<string, unknown>; summary: Record<string, unknown> }>(handler, {
        params: { agentKey: AGENT_KEY },
        query: { page: '1', limit: '2' },
      })
      const page2 = await call<{ conversations: Array<{ title: string }> }>(handler, { params: { agentKey: AGENT_KEY }, query: { page: '2', limit: '2' } })

      expect(page1.body.conversations.map((c) => c.title)).to.deep.equal(['archived 3', 'archived 2'])
      expect(page2.body.conversations.map((c) => c.title)).to.deep.equal(['archived 1'])
      expect(page1.body.pagination).to.include({ totalCount: 3, hasNextPage: true })
      expect(page1.body.summary).to.include({ totalArchived: 3 })
    })
  })

  describe('listAllAgentsArchivedConversationsGrouped', () => {
    function stubAggregate(groups: unknown[], totalAgentCount: number): sinon.SinonStub {
      return sinon.stub(ChatSession, 'aggregate').callsFake(((pipeline: Array<Record<string, unknown>>) =>
        Promise.resolve(pipeline.some((stage) => '$count' in stage) ? [{ totalAgentCount }] : groups)) as never)
    }

    it('hides archives of agents that were deleted and reports agent-level paging', async () => {
      const { ai } = fresh()
      ai.reply(/\/api\/v1\/agent\/\?page=1&/, 200, { agents: [{ _key: 'gone-1' }, { id: 7 }, { _key: '' }], pagination: { hasNext: true } })
      ai.reply(/\/api\/v1\/agent\/\?page=2&/, 200, { agents: [{ _key: 'gone-2' }], pagination: { hasNext: false } })
      const conversation = { _id: oid(), userId: OWNER, initiator: OWNER, updatedAt: new Date('2026-01-02'), archivedBy: OWNER, sharedWith: [] }
      const aggregate = stubAggregate([{ agentKey: AGENT_KEY, conversations: [conversation], totalCount: 7 }], 12)

      const out = await call<{ groups: Array<Record<string, unknown>>; agentPagination: Record<string, unknown> }>(
        controller.listAllAgentsArchivedConversationsGrouped(appConfig) as JsonHandler,
        { query: { agentPage: '2', agentLimit: '5' } },
      )

      const match = (aggregate.firstCall.args[0] as Array<{ $match?: Record<string, unknown> }>)[0]?.$match
      expect(match?.agentKey).to.deep.equal({ $nin: ['gone-1', '7', 'gone-2'] })
      expect(String((match?.$or as Array<{ userId: unknown }>)[0]?.userId)).to.equal(String(OWNER))
      expect(String(match?.orgId)).to.equal(String(ORG))
      const dataPipeline = aggregate.secondCall.args[0] as Array<Record<string, unknown>>
      expect(dataPipeline).to.deep.include({ $skip: 5 })
      expect(dataPipeline).to.deep.include({ $limit: 5 })
      expect(out.body.agentPagination).to.include({ page: 2, limit: 5, totalCount: 12, totalPages: 3, hasNextPage: true, hasPrevPage: true })
      const group = out.body.groups[0] as { pagination: Record<string, unknown>; conversations: Array<{ archivedAt: Date }> }
      expect(group.pagination).to.include({ totalCount: 7, hasNextPage: true })
      expect(group.conversations[0]?.archivedAt).to.deep.equal(conversation.updatedAt)
    })

    it('still lists archives when the AI service cannot say which agents were deleted', async () => {
      const { ai } = fresh()
      ai.reply(/\/api\/v1\/agent\/\?/, 503, { detail: 'down' })
      const aggregate = stubAggregate([], 0)

      const out = await call<{ agentPagination: Record<string, unknown> }>(
        controller.listAllAgentsArchivedConversationsGrouped(appConfig) as JsonHandler,
        { query: { agentPage: '-3', agentLimit: '1000' } },
      )

      const match = (aggregate.firstCall.args[0] as Array<{ $match?: Record<string, unknown> }>)[0]?.$match
      expect(match).to.not.have.property('agentKey')
      expect(out.body.agentPagination).to.include({ page: 1, limit: 100, totalCount: 0, hasNextPage: false })
    })

    it('refuses a request with no signed-in user', async () => {
      fresh()
      const out = await call(controller.listAllAgentsArchivedConversationsGrouped(appConfig) as JsonHandler, { user: {} as never })
      expect(out.error?.statusCode).to.equal(400)
    })
  })

  describe('fetchDeletedAgentKeysForUser', () => {
    it('returns null, not an empty list, when the AI service is unreachable, so nothing is hidden by mistake', async () => {
      const { ai } = fresh()
      ai.failNetwork(/\/api\/v1\/agent\//, Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } }))
      const clock = sinon.useFakeTimers({ toFake: ['setTimeout'] })
      const pending = controller.fetchDeletedAgentKeysForUser(appConfig, { headers: {} } as never)
      await clock.tickAsync(5000)
      clock.restore()
      expect(await pending).to.equal(null)
    })
  })

  describe('agent usage lookups', () => {
    it('getWebSearchProviderUsage: passes the AI service answer through, and an empty list when it has none', async () => {
      const { ai } = fresh()
      ai.reply(/web-search-usage\/tavily/, 200, { success: true, agents: [{ name: 'Research bot' }] })
      ai.reply(/web-search-usage\/serper/, 500, { detail: 'boom' })
      const handler = controller.getWebSearchProviderUsage(appConfig) as JsonHandler

      const used = await call(handler, { params: { provider: 'tavily' } })
      const unknown = await call(handler, { params: { provider: 'serper' } })
      const missing = await call(handler, { params: {} })

      expect(used.body).to.deep.equal({ success: true, agents: [{ name: 'Research bot' }] })
      expect(unknown.body).to.deep.equal({ success: true, agents: [] })
      expect(missing.error?.statusCode).to.equal(400)
      expect(ai.calls.map((c) => c.url)).to.deep.equal([
        'http://ai.test/api/v1/agent/web-search-usage/tavily',
        'http://ai.test/api/v1/agent/web-search-usage/serper',
      ])
    })
  })

  describe('cancelConversationStream', () => {
    it('asks the AI service to stop the run for this conversation, and passes a not-found answer back', async () => {
      const { store, ai } = fresh()
      const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
      ai.reply(/\/api\/v1\/chat\/cancel/, 200, { cancelled: true })

      const out = await call(controller.cancelConversationStream(appConfig) as JsonHandler, {
        params: { conversationId: String(session._id) },
        body: { runId: 'run-9' },
      })

      expect(out.body).to.deep.equal({ cancelled: true })
      expect(ai.calls[0]?.body).to.deep.equal({ runId: 'run-9', conversationId: String(session._id) })
    })
  })

  describe('setConversationProjectVisibility', () => {
    it('refuses to change visibility of a chat that is not in a project, and changes it once it is', async () => {
      const { store } = fresh()
      const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
      const params = { conversationId: String(session._id) }

      const refused = await call(controller.setConversationProjectVisibility as JsonHandler, { params, body: { visibility: 'project' } })
      expect(refused.error?.statusCode).to.equal(400)
      expect(refused.error?.message).to.equal('Conversation is not linked to a project')

      session.set('projectId', oid())
      const changed = await call(controller.setConversationProjectVisibility as JsonHandler, { params, body: { visibility: 'project' } })
      expect(changed.body).to.deep.include({ projectVisibility: 'project' })
      expect(store.session(session._id)?.projectVisibility).to.equal('project')
    })
  })

  describe('hydrateScopedRequestAsUser: a Slack bot asking on behalf of someone', () => {
    const encryptedSlackConfig = (agentIds: string[]): string => {
      const config = loadConfigurationManagerConfig()
      return EncryptionService.getInstance(config.algorithm, config.secretKey).encrypt(
        JSON.stringify({ configs: agentIds.map((agentId) => ({ agentId })) }),
      )
    }
    const kv = (agentIds: string[]): never => ({ get: sinon.stub().resolves(encryptedSlackConfig(agentIds)) }) as never
    const serviceRequest = (): Record<string, unknown> & { headers: Record<string, string>; user?: Record<string, unknown> } => ({
      headers: { authorization: 'Bearer scoped' },
      params: { agentKey: AGENT_KEY },
      tokenPayload: { email: 'Guest@Example.com' },
      context: {},
    })

    const previousSecret = process.env.SECRET_KEY
    before(() => {
      process.env.SECRET_KEY = previousSecret ?? 'slack-bot-test-secret'
    })
    after(() => {
      if (previousSecret === undefined) delete process.env.SECRET_KEY
      else process.env.SECRET_KEY = previousSecret
    })

    beforeEach(() => {
      sinon.stub(Users, 'findOne').resolves(null)
      sinon.stub(Org, 'findOne').resolves({ _id: ORG } as never)
    })

    it('lets an email without an account in only for an agent set up for Slack that is a service account', async () => {
      const { ai } = fresh()
      ai.reply(/internal\/service-account/, 200, { isServiceAccount: true })
      const req = serviceRequest()

      await controller.hydrateScopedRequestAsUser(req as never, appConfig, kv([AGENT_KEY]))

      expect(ai.calls[0]?.url).to.equal(`http://ai.test/api/v1/agent/${AGENT_KEY}/internal/service-account`)
      expect(String(req.user?.userId)).to.equal(controller.stableObjectIdHexForExternalEmail('guest@example.com'))
      expect(req.user).to.include({ isServiceAccount: true })
      expect(req.user?.scopes).to.deep.equal([TokenScopes.CONVERSATION_CREATE])
      const token = jwt.verify((req.headers.authorization ?? '').replace('Bearer ', ''), 'test-scoped-secret') as Record<string, unknown>
      expect(token).to.include({ isServiceAccount: true, email: 'Guest@Example.com' })
    })

    const refusals: Array<{ name: string; agents: string[]; reply?: [number, unknown] }> = [
      { name: 'the agent is not set up for Slack', agents: ['another-agent'] },
      { name: 'the agent is not a service account', agents: [AGENT_KEY], reply: [200, { isServiceAccount: false }] },
      { name: 'the AI service cannot confirm it', agents: [AGENT_KEY], reply: [500, { detail: 'boom' }] },
    ]
    for (const r of refusals) {
      it(`refuses an email without an account when ${r.name}`, async () => {
        const { ai } = fresh()
        if (r.reply) ai.reply(/internal\/service-account/, r.reply[0], r.reply[1])
        const req = serviceRequest()

        let error: (Error & { statusCode?: number }) | undefined
        try {
          await controller.hydrateScopedRequestAsUser(req as never, appConfig, kv(r.agents))
        } catch (e) {
          error = e as Error & { statusCode?: number }
        }

        expect(error?.statusCode).to.equal(404)
        expect(error?.message).to.equal('User not found, create an account on the Pipeshub platform first.')
        expect(req.user).to.equal(undefined)
        expect(req.headers.authorization).to.equal('Bearer scoped')
      })
    }
  })
})
