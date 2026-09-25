import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OTHER_ORG = oid()
const OWNER = oid()
const STRANGER = oid()
const RECIPIENT = oid()
const OUTSIDER = oid()
const AGENT_KEY = 'agent-1'

type User = { userId: Types.ObjectId; orgId: Types.ObjectId }
const owner: User = { userId: OWNER, orgId: ORG }
const stranger: User = { userId: STRANGER, orgId: ORG }
const recipient: User = { userId: RECIPIENT, orgId: ORG }
const outsider: User = { userId: OUTSIDER, orgId: OTHER_ORG }

interface Setup {
  store: InMemoryChatStore
  ai: FakeAIBackend
  chatId: string
  botMessageId: string
  agentChatId: string
  agentBotMessageId: string
}

function setup(): Setup {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()

  const chat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Quarterly numbers',
    sessionType: 'chat',
    isShared: true,
    sharedWith: [{ userId: RECIPIENT, accessLevel: 'read' }],
  })
  store.addMessage(chat, { messageType: 'user_query', content: 'What were Q3 sales?' })
  const bot = store.addMessage(chat, { messageType: 'bot_response', content: 'Q3 sales were 4M.' })

  const agentChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Agent chat',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    conversationSource: 'agent_chat',
  })
  store.addMessage(agentChat, { messageType: 'user_query', content: 'Summarise the roadmap' })
  const agentBot = store.addMessage(agentChat, { messageType: 'bot_response', content: 'The roadmap has 3 themes.' })

  store.writes.length = 0
  return {
    store,
    ai,
    chatId: String(chat._id),
    botMessageId: String(bot._id),
    agentChatId: String(agentChat._id),
    agentBotMessageId: String(agentBot._id),
  }
}

function request(user: User, params: Record<string, string>, body: Record<string, unknown> = {}): never {
  return {
    headers: { authorization: 'Bearer token' },
    params,
    body,
    query: {},
    user: { ...user, email: 'someone@example.com' },
    context: { requestId: 'req-ownership' },
  } as never
}

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type StreamHandler = (req: never, res: never) => Promise<unknown>

interface JsonOutcome {
  status: number | undefined
  body: unknown
  error: (Error & { statusCode?: number }) | undefined
}

async function callJson(handler: JsonHandler, req: never): Promise<JsonOutcome> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody, error: next.firstCall?.args[0] as JsonOutcome['error'] }
}

async function callStream(handler: StreamHandler, req: never): Promise<FakeSSEResponse> {
  const res = new FakeSSEResponse()
  await handler(req, res as never)
  await settle()
  return res
}

describe('es_controller ownership: nobody reads or changes a conversation that is not theirs', () => {
  afterEach(() => {
    sinon.restore()
  })

  interface JsonCase {
    name: string
    handler: () => JsonHandler
    params: (s: Setup) => Record<string, string>
    body?: Record<string, unknown>
  }

  const chatCases: JsonCase[] = [
    { name: 'getConversationById', handler: () => controller.getConversationById as JsonHandler, params: (s) => ({ conversationId: s.chatId }) },
    { name: 'addMessage', handler: () => controller.addMessage(appConfig) as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { query: 'And Q4?' } },
    { name: 'updateTitle', handler: () => controller.updateTitle as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { title: 'Hijacked' } },
    { name: 'updateFeedback', handler: () => controller.updateFeedback as JsonHandler, params: (s) => ({ conversationId: s.chatId, messageId: s.botMessageId }), body: { isHelpful: false } },
    { name: 'archiveConversation', handler: () => controller.archiveConversation as JsonHandler, params: (s) => ({ conversationId: s.chatId }) },
    { name: 'deleteConversationById', handler: () => controller.deleteConversationById as JsonHandler, params: (s) => ({ conversationId: s.chatId }) },
    { name: 'shareConversationById', handler: () => controller.shareConversationById(appConfig) as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { userIds: [String(oid())] } },
    { name: 'unshareConversationById', handler: () => controller.unshareConversationById(appConfig) as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { userIds: [String(RECIPIENT)] } },
    { name: 'setConversationProject', handler: () => controller.setConversationProject as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { projectId: null } },
    { name: 'setConversationProjectVisibility', handler: () => controller.setConversationProjectVisibility as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { visibility: 'project' } },
    { name: 'cancelConversationStream', handler: () => controller.cancelConversationStream(appConfig) as JsonHandler, params: (s) => ({ conversationId: s.chatId }), body: { runId: 'run-1' } },
  ]

  const agentCases: JsonCase[] = [
    { name: 'getAgentConversationById', handler: () => controller.getAgentConversationById as JsonHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY }) },
    { name: 'updateAgentConversationTitle', handler: () => controller.updateAgentConversationTitle as JsonHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY }), body: { title: 'Hijacked' } },
    { name: 'updateAgentFeedback', handler: () => controller.updateAgentFeedback as JsonHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY, messageId: s.agentBotMessageId }), body: { isHelpful: false } },
    { name: 'archiveAgentConversation', handler: () => controller.archiveAgentConversation as JsonHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY }) },
    { name: 'cancelAgentConversationStream', handler: () => controller.cancelAgentConversationStream(appConfig) as JsonHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY }), body: { runId: 'run-1' } },
  ]

  for (const c of [...chatCases, ...agentCases]) {
    it(`${c.name}: another user in the same org gets 404 and nothing is written or sent to the AI service`, async () => {
      const s = setup()
      const before = JSON.stringify(s.store.sessions.map((d) => d.toObject()))

      const out = await callJson(c.handler(), request(stranger, c.params(s), c.body))

      expect(out.error?.statusCode, `${c.name} response`).to.equal(404)
      expect(out.status).to.equal(undefined)
      expect(s.store.writes, 'writes').to.deep.equal([])
      expect(JSON.stringify(s.store.sessions.map((d) => d.toObject()))).to.equal(before)
      expect(s.ai.calls, 'AI service calls').to.deep.equal([])
    })
  }

  for (const c of chatCases.filter((x) => x.name !== 'getConversationById' && x.name !== 'updateFeedback')) {
    it(`${c.name}: a read-only recipient of a shared conversation gets 404 and nothing is written`, async () => {
      const s = setup()

      const out = await callJson(c.handler(), request(recipient, c.params(s), c.body))

      expect(out.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })
  }

  it('getConversationById: a user from another organisation gets 404 even with the right id', async () => {
    const s = setup()
    const out = await callJson(controller.getConversationById as JsonHandler, request(outsider, { conversationId: s.chatId }))
    expect(out.error?.statusCode).to.equal(404)
  })

  it('getConversationById: the owner and a recipient it was shared with can read it', async () => {
    const s = setup()
    for (const user of [owner, recipient]) {
      const out = await callJson(controller.getConversationById as JsonHandler, request(user, { conversationId: s.chatId }))
      expect(out.status).to.equal(200)
      const conversation = (out.body as { conversation: { title: string; messages: unknown[] } }).conversation
      expect(conversation.title).to.equal('Quarterly numbers')
      expect(conversation.messages).to.have.length(2)
    }
  })

  it('getConversationById: a project member reads a chat only when its owner made it visible to the project', async () => {
    const s = setup()
    const projectId = oid()
    const chat = s.store.session(s.chatId)
    chat?.set({ projectId, projectVisibility: 'private' })
    ;(ProjectService.getAccessibleProjectIds as sinon.SinonStub).resolves([projectId])

    const hidden = await callJson(controller.getConversationById as JsonHandler, request(stranger, { conversationId: s.chatId }))
    expect(hidden.error?.statusCode).to.equal(404)

    chat?.set({ projectVisibility: 'project' })
    const visible = await callJson(controller.getConversationById as JsonHandler, request(stranger, { conversationId: s.chatId }))
    expect(visible.status).to.equal(200)
  })

  it('updateFeedback: the owner and a recipient it was shared with can rate an answer', async () => {
    const s = setup()
    for (const user of [owner, recipient]) {
      const out = await callJson(
        controller.updateFeedback as JsonHandler,
        request(user, { conversationId: s.chatId, messageId: s.botMessageId }, { isHelpful: true }),
      )
      expect(out.status, 'feedback status').to.equal(200)
    }
    const feedback = s.store.messagesOf(s.chatId)[1]?.feedback as Array<{ feedbackProvider: Types.ObjectId }>
    expect(feedback.map((f) => String(f.feedbackProvider))).to.deep.equal([String(OWNER), String(RECIPIENT)])
  })

  it('updateFeedback: sharing a conversation with one person does not let the rest of the org rate it', async () => {
    const s = setup()
    const out = await callJson(
      controller.updateFeedback as JsonHandler,
      request(stranger, { conversationId: s.chatId, messageId: s.botMessageId }, { isHelpful: false }),
    )
    expect(out.error?.statusCode).to.equal(404)
    expect(s.store.messagesOf(s.chatId)[1]?.feedback).to.deep.equal([])
  })

  it('owner controls still work: rename, archive and delete change the owner’s own conversation', async () => {
    const s = setup()
    const renamed = await callJson(controller.updateTitle as JsonHandler, request(owner, { conversationId: s.chatId }, { title: 'Q3 review' }))
    expect(renamed.status).to.equal(200)
    expect(s.store.session(s.chatId)?.title).to.equal('Q3 review')

    const archived = await callJson(controller.archiveConversation as JsonHandler, request(owner, { conversationId: s.chatId }))
    expect(archived.status).to.equal(200)
    expect(s.store.session(s.chatId)?.isArchived).to.equal(true)

    const deletedAgent = await callJson(
      controller.deleteAgentConversationById as JsonHandler,
      request(owner, { conversationId: s.agentChatId, agentKey: AGENT_KEY }),
    )
    expect(deletedAgent.status).to.equal(200)
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(true)
  })

  // The published contract makes this delete idempotent: nothing matched is still a 200 with `conversation: null`.
  it('deleteAgentConversationById: another user, a wrong agent or a missing id get the idempotent 200, and nothing is deleted', async () => {
    const s = setup()
    const attempts = [
      request(stranger, { conversationId: s.agentChatId, agentKey: AGENT_KEY }),
      request(owner, { conversationId: s.agentChatId, agentKey: 'agent-2' }),
      request(owner, { conversationId: String(oid()), agentKey: AGENT_KEY }),
    ]
    for (const req of attempts) {
      const out = await callJson(controller.deleteAgentConversationById as JsonHandler, req)
      expect(out.status).to.equal(200)
      expect(out.body).to.deep.equal({ message: 'Conversation deleted successfully', conversation: null })
    }
    expect(s.store.writes).to.deep.equal([])
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(false)
  })

  it('deleteAgentConversationById: a database failure during the lookup is an error, not the idempotent 200', async () => {
    const s = setup()
    ;(ChatSession.findOne as unknown as sinon.SinonStub).rejects(new Error('connection to mongo-0.internal:27017 closed'))

    const out = await callJson(
      controller.deleteAgentConversationById as JsonHandler,
      request(owner, { conversationId: s.agentChatId, agentKey: AGENT_KEY }),
    )

    expect(out.status, 'no success response').to.equal(undefined)
    expect(out.error?.message).to.match(/mongo-0/)
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(false)
  })

  interface StreamCase {
    name: string
    handler: () => StreamHandler
    params: (s: Setup) => Record<string, string>
    body: Record<string, unknown>
  }

  const streamCases: StreamCase[] = [
    { name: 'addMessageStream', handler: () => controller.addMessageStream(appConfig) as StreamHandler, params: (s) => ({ conversationId: s.chatId }), body: { query: 'And Q4?' } },
    { name: 'regenerateAnswers', handler: () => controller.regenerateAnswers(appConfig) as StreamHandler, params: (s) => ({ conversationId: s.chatId, messageId: s.botMessageId }), body: {} },
    { name: 'addMessageStreamToAgentConversation', handler: () => controller.addMessageStreamToAgentConversation(appConfig) as StreamHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY }), body: { query: 'More detail' } },
    { name: 'regenerateAgentAnswers', handler: () => controller.regenerateAgentAnswers(appConfig) as StreamHandler, params: (s) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY, messageId: s.agentBotMessageId }), body: {} },
  ]

  for (const c of streamCases) {
    for (const [who, user] of [['another user', stranger], ['a read-only recipient', recipient]] as const) {
      it(`${c.name}: ${who} is told the conversation was not found, and no answer is generated or saved`, async () => {
        const s = setup()

        const res = await callStream(c.handler(), request(user, c.params(s), c.body))

        const errors = res.eventsOf('RUN_ERROR')
        expect(errors, 'RUN_ERROR frames').to.have.length(1)
        expect(errors[0]?.data.message).to.match(/not found/i)
        expect(res.writableEnded).to.equal(true)
        expect(s.ai.streamCalls).to.deep.equal([])
        expect(s.store.writes.filter((w) => w.startsWith('message.'))).to.deep.equal([])
        expect(s.store.messagesOf(s.chatId)).to.have.length(2)
        expect(s.store.messagesOf(s.agentChatId)).to.have.length(2)
      })
    }
  }
})
