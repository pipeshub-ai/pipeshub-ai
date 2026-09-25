import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never
const ORG = oid()
const OWNER = oid()
const AGENT_KEY = 'agent-1'
const STACK_FILE = 'secret-internal.ts'
const savedError = {
  message: CHAT_ERROR_MESSAGES.failed,
  errorType: 'internal_error',
  stack: `Error: boom\n    at /srv/pipeshub/${STACK_FILE}:42`,
}

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

async function respond(
  handler: JsonHandler,
  { params = {}, query = {}, body = {} }: { params?: Record<string, string>; query?: Record<string, string>; body?: Record<string, unknown> },
): Promise<string> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: {}, params, query, body, user: { userId: OWNER, orgId: ORG }, context: {} }
  await handler(req as never, res as never, next as never)
  await settle()
  expect(next.called, `handler failed: ${String(next.firstCall?.args[0])}`).to.equal(false)
  expect(res.statusCode).to.equal(200)
  return JSON.stringify(res.jsonBody)
}

function seed(): { store: InMemoryChatStore; chatId: string; agentChatId: string } {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Failed chat', status: 'Failed', conversationErrors: [savedError] })
  const agentChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Failed agent chat',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    status: 'Failed',
    conversationErrors: [savedError],
  })
  return { store, chatId: String(chat._id), agentChatId: String(agentChat._id) }
}

const expectNoStack = (json: string): void => {
  expect(json, 'the saved error is still reported').to.contain(CHAT_ERROR_MESSAGES.failed)
  expect(json, 'but not its server stack trace').to.not.contain(STACK_FILE)
}

describe('es_controller: server stack traces saved with a failed answer never reach the browser', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('getAllConversations (the chat sidebar)', async () => {
    seed()
    expectNoStack(await respond(controller.getAllConversations as JsonHandler, {}))
  })

  it('getAllAgentConversations (an agent’s chat list)', async () => {
    seed()
    expectNoStack(await respond(controller.getAllAgentConversations as JsonHandler, { params: { agentKey: AGENT_KEY } }))
  })

  it('updateTitle and updateAgentConversationTitle', async () => {
    const { chatId, agentChatId } = seed()
    expectNoStack(await respond(controller.updateTitle as JsonHandler, { params: { conversationId: chatId }, body: { title: 'Renamed' } }))
    expectNoStack(
      await respond(controller.updateAgentConversationTitle as JsonHandler, {
        params: { conversationId: agentChatId, agentKey: AGENT_KEY },
        body: { title: 'Renamed' },
      }),
    )
  })

  it('deleteAgentConversationById', async () => {
    const { store, agentChatId } = seed()
    expectNoStack(await respond(controller.deleteAgentConversationById as JsonHandler, { params: { conversationId: agentChatId, agentKey: AGENT_KEY } }))
    expect(store.session(agentChatId)?.conversationErrors?.[0]?.stack, 'the stack stays in the database').to.contain(STACK_FILE)
  })

  it('the archived lists and archive search', async () => {
    const { store, chatId, agentChatId } = seed()
    for (const id of [chatId, agentChatId]) store.session(id)?.set({ isArchived: true, archivedBy: OWNER })
    const archived = store.sessions.map((s) => s.toObject())

    expectNoStack(await respond(controller.listAllArchivesConversation as JsonHandler, {}))
    expectNoStack(await respond(controller.listAllArchivesAgentConversation() as JsonHandler, { params: { agentKey: AGENT_KEY } }))

    sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
    const aggregate = sinon.stub(ChatSession, 'aggregate')
    aggregate.resolves(archived as never)
    expectNoStack(await respond(controller.searchArchivedConversations(appConfig) as JsonHandler, { query: { search: 'Failed' } }))

    aggregate.callsFake(((pipeline: Array<Record<string, unknown>>) =>
      Promise.resolve(
        pipeline.some((stage) => '$count' in stage)
          ? [{ totalAgentCount: 1 }]
          : [{ agentKey: AGENT_KEY, conversations: [archived[1]], totalCount: 1 }],
      )) as never)
    expectNoStack(await respond(controller.listAllAgentsArchivedConversationsGrouped(appConfig) as JsonHandler, {}))
  })
})
