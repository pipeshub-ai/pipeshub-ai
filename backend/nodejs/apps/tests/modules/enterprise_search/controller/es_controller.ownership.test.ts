import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeAIBackend, InMemoryChatStore, oid } from './chat-test-harness'
import { invokeRoute, RouteOutcome } from '../helpers/route-invoker'
import { buildRouters, Env } from '../helpers/conversation-world'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { IConversationEventProducers, useConversationEventProducers } from '../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { asUser as personAsUser, Fixture, id as personId, PEOPLE, startFixture } from '../helpers/collab-fixture'
import { CONVERSATION_ROUTES, ConversationRoute, urlOf } from '../helpers/conversation-routes'

/**
 * Nobody reads or changes a conversation that is not theirs, now asked through the routers (guard and handler together).
 * The cases are kept from the handler-level version; the ones that decide access are superseded by
 * conversation-access.matrix.test.ts, which covers them for every route and actor, and are marked so in their titles.
 */
const SUPERSEDED = ' [superseded by conversation-access.matrix.test.ts]'

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

const route = (id: string): ConversationRoute => CONVERSATION_ROUTES.find((r) => r.id === id)!

function asUser(user: User): Record<string, unknown> {
  return { ...user, email: 'someone@example.com' }
}

async function call(
  env: Env,
  id: string,
  user: User,
  s: Setup,
  options: { body?: Record<string, unknown>; conversationId?: string; agentKey?: string } = {},
): Promise<RouteOutcome> {
  const r = route(id)
  return invokeRoute(env[r.kind], {
    method: r.method,
    url: urlOf(r, {
      conversationId: options.conversationId ?? (r.kind === 'agent' ? s.agentChatId : s.chatId),
      messageId: r.kind === 'agent' ? s.agentBotMessageId : s.botMessageId,
      agentKey: options.agentKey ?? AGENT_KEY,
    }),
    body: options.body ?? r.body,
    user: asUser(user),
  })
}

const errorCode = (out: RouteOutcome): string | undefined => (out.body as { error?: { code?: string } } | undefined)?.error?.code

describe('es_controller ownership: nobody reads or changes a conversation that is not theirs', () => {
  let env: Env
  before(() => {
    env = buildRouters({ collab: false, stop: false })
  })
  afterEach(() => {
    sinon.restore()
  })

  interface DenialCase {
    name: string
    id: string
    body?: Record<string, unknown>
  }

  const denialCases: DenialCase[] = [
    { name: 'getConversationById', id: 'C5' },
    { name: 'addMessage', id: 'C1', body: { query: 'And Q4?' } },
    { name: 'updateTitle', id: 'C13', body: { title: 'Hijacked' } },
    { name: 'updateFeedback', id: 'C14', body: { isHelpful: false } },
    { name: 'archiveConversation', id: 'C15' },
    { name: 'deleteConversationById', id: 'C6' },
    { name: 'shareConversationById', id: 'C7', body: { userIds: [String(oid())] } },
    { name: 'unshareConversationById', id: 'C8', body: { userIds: [String(RECIPIENT)] } },
    { name: 'setConversationProject', id: 'C9', body: { projectId: null } },
    { name: 'setConversationProjectVisibility', id: 'C10', body: { visibility: 'project' } },
    { name: 'cancelConversationStream', id: 'C12' },
  ]

  // Agent routes: strangers, recipients and wrong agent keys are refused by the route guard; see agent-routes.access.test.ts.
  for (const c of denialCases) {
    it(`${c.name}: another user in the same org gets 404 and nothing is written or sent to the AI service${SUPERSEDED}`, async () => {
      const s = setup()
      const before = JSON.stringify(s.store.sessions.map((d) => d.toObject()))

      const out = await call(env, c.id, stranger, s, { body: c.body })

      expect(out.error?.statusCode, `${c.name} response`).to.equal(404)
      expect(out.status).to.equal(404)
      expect(errorCode(out)).to.equal('CONVERSATION_NOT_FOUND')
      expect(s.store.writes, 'writes').to.deep.equal([])
      expect(JSON.stringify(s.store.sessions.map((d) => d.toObject()))).to.equal(before)
      expect(s.ai.calls, 'AI service calls').to.deep.equal([])
    })
  }

  for (const c of denialCases.filter((x) => x.name !== 'getConversationById' && x.name !== 'updateFeedback')) {
    it(`${c.name}: a read-only recipient of a shared conversation gets 404 and nothing is written${SUPERSEDED}`, async () => {
      const s = setup()

      const out = await call(env, c.id, recipient, s, { body: c.body })

      expect(out.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })
  }

  it(`getConversationById: a user from another organisation gets 404 even with the right id${SUPERSEDED}`, async () => {
    const s = setup()
    const out = await call(env, 'C5', outsider, s)
    expect(out.error?.statusCode).to.equal(404)
  })

  it('getConversationById: the owner and a recipient it was shared with can read it', async () => {
    const s = setup()
    for (const user of [owner, recipient]) {
      const out = await call(env, 'C5', user, s)
      expect(out.status).to.equal(200)
      const conversation = (out.body as { conversation: { title: string; messages: unknown[] } }).conversation
      expect(conversation.title).to.equal('Quarterly numbers')
      expect(conversation.messages).to.have.length(2)
    }
  })

  it(`getConversationById: a project member reads a chat only when its owner made it visible to the project${SUPERSEDED}`, async () => {
    const s = setup()
    const projectId = oid()
    const chat = s.store.session(s.chatId)
    chat?.set({ projectId, projectVisibility: 'private' })
    sinon.stub(Project, 'findOne').returns({
      lean: () => Promise.resolve({ orgId: ORG, userId: OWNER, visibility: 'private', members: [{ principalType: 'user', principalId: STRANGER, role: 'viewer' }], aclVersion: 0 }),
    } as never)

    const hidden = await call(env, 'C5', stranger, s)
    expect(hidden.error?.statusCode).to.equal(404)

    chat?.set({ projectVisibility: 'project' })
    const visible = await call(env, 'C5', stranger, s)
    expect(visible.status).to.equal(200)
  })

  it('updateFeedback: the owner and a recipient it was shared with can rate an answer', async () => {
    const s = setup()
    for (const user of [owner, recipient]) {
      const out = await call(env, 'C14', user, s, { body: { isHelpful: true } })
      expect(out.status, 'feedback status').to.equal(200)
    }
    const feedback = s.store.messagesOf(s.chatId)[1]?.feedback as Array<{ feedbackProvider: Types.ObjectId }>
    expect(feedback.map((f) => String(f.feedbackProvider))).to.deep.equal([String(OWNER), String(RECIPIENT)])
  })

  it(`updateFeedback: sharing a conversation with one person does not let the rest of the org rate it${SUPERSEDED}`, async () => {
    const s = setup()
    const out = await call(env, 'C14', stranger, s, { body: { isHelpful: false } })
    expect(out.error?.statusCode).to.equal(404)
    expect(s.store.messagesOf(s.chatId)[1]?.feedback).to.deep.equal([])
  })

  it('owner controls still work: rename, archive and delete change the owner’s own conversation', async () => {
    const s = setup()
    const renamed = await call(env, 'C13', owner, s, { body: { title: 'Q3 review' } })
    expect(renamed.status).to.equal(200)
    expect(s.store.session(s.chatId)?.title).to.equal('Q3 review')

    const archived = await call(env, 'C15', owner, s)
    expect(archived.status).to.equal(200)
    expect(s.store.session(s.chatId)?.isArchived).to.equal(true)

    const deletedAgent = await call(env, 'A8', owner, s)
    expect(deletedAgent.status).to.equal(200)
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(true)
  })

  // The handler-level version asserted an idempotent 200 for a granted conversation that is gone or belongs to another
  // agent. The route guard now answers 404 first (DV-3), so the conversation is never touched.
  it(`deleteAgentConversationById: a conversation that is gone or belongs to another agent is a 404, and nothing is deleted${SUPERSEDED}`, async () => {
    const s = setup()
    const attempts = [
      await call(env, 'A8', owner, s, { agentKey: 'agent-2' }),
      await call(env, 'A8', owner, s, { conversationId: String(oid()) }),
    ]
    for (const out of attempts) {
      expect(out.error?.statusCode).to.equal(404)
      expect(errorCode(out)).to.equal('CONVERSATION_NOT_FOUND')
    }
    expect(s.store.writes).to.deep.equal([])
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(false)
  })

  it('deleteAgentConversationById: a database failure during the lookup is an error, not a delete', async () => {
    const s = setup()
    const failure = new Error('connection to mongo-0.internal:27017 closed')
    ;(ChatSession.findOne as unknown as sinon.SinonStub).returns({
      select: () => ({ lean: () => Promise.reject(failure) }),
    })

    const out = await call(env, 'A8', owner, s)

    expect(out.status).to.equal(500)
    expect(out.error?.message).to.match(/mongo-0/)
    expect(JSON.stringify(out.body)).to.not.match(/mongo-0/)
    expect(s.store.session(s.agentChatId)?.isDeleted).to.equal(false)
  })

  for (const [name, id] of [
    ['deleteConversation', 'C6'],
    ['archiveConversation', 'C15'],
    ['unarchiveConversation', 'C16'],
  ] as const) {
    it(`PH01-10: ${name}: a write recipient gets 404 and nothing is written${SUPERSEDED}`, async () => {
      const s = setup()
      const shared = s.store.addSession({
        orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Shared', sessionType: 'chat',
        isShared: true, isArchived: name === 'unarchiveConversation',
        sharedWith: [{ userId: RECIPIENT, accessLevel: 'write' }],
      })
      s.store.writes.length = 0
      const out = await call(env, id, recipient, s, { conversationId: String(shared._id) })
      expect(out.error?.statusCode).to.equal(404)
      expect(s.store.writes).to.deep.equal([])
    })
  }

  describe('shareConversationById access level', () => {
    function shareSetup(existing: Array<{ userId: Types.ObjectId; accessLevel: string }> = []) {
      const s = setup()
      const chat = s.store.addSession({
        orgId: ORG, userId: OWNER, initiator: OWNER, title: 'To share', sessionType: 'chat',
        isShared: existing.length > 0, sharedWith: existing,
      })
      return { s, id: String(chat._id) }
    }

    it('PH01-09: write is stored as read and the response warns ACCESS_LEVEL_DOWNGRADED', async () => {
      const { s, id } = shareSetup()
      const out = await call(env, 'C7', owner, s, { conversationId: id, body: { userIds: [String(RECIPIENT)], accessLevel: 'write' } })
      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(200)
      expect((out.body as { warnings?: unknown }).warnings).to.deep.equal([
        { code: 'ACCESS_LEVEL_DOWNGRADED', requested: 'write', applied: 'read' },
      ])
      const rows = s.store.session(id)?.sharedWith as Array<{ userId: unknown; accessLevel: string }>
      expect(rows.map((r) => [String(r.userId), r.accessLevel])).to.deep.equal([[String(RECIPIENT), 'read']])
    })

    it('PH01-09: re-sharing an existing write row stores read', async () => {
      const { s, id } = shareSetup([{ userId: RECIPIENT, accessLevel: 'write' }])
      const out = await call(env, 'C7', owner, s, { conversationId: id, body: { userIds: [String(RECIPIENT)], accessLevel: 'write' } })
      expect(out.status).to.equal(200)
      const rows = s.store.session(id)?.sharedWith as Array<{ accessLevel: string }>
      expect(rows.map((r) => r.accessLevel)).to.deep.equal(['read'])
    })

    it('read or omitted access level stores read with no warning', async () => {
      const { s, id } = shareSetup()
      const out = await call(env, 'C7', owner, s, { conversationId: id, body: { userIds: [String(RECIPIENT)] } })
      expect(out.status).to.equal(200)
      expect(out.body).to.not.have.property('warnings')
    })
  })

  describe('SEC-03: a bare isShared flag grants no regenerate', () => {
    it(`regenerateAnswers: a same-org stranger gets 404 and nothing reaches the AI${SUPERSEDED}`, async () => {
      const s = setup()
      const open = s.store.addSession({
        orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Flagged shared', sessionType: 'chat',
        isShared: true, sharedWith: [],
      })
      const bot = s.store.addMessage(open, { messageType: 'bot_response', content: 'answer' })
      s.store.writes.length = 0
      const r = route('C11')
      const out = await invokeRoute(env.chat, {
        method: r.method,
        url: urlOf(r, { conversationId: String(open._id), messageId: String(bot._id) }),
        body: r.body,
        user: asUser(stranger),
      })
      expect(out.error?.statusCode).to.equal(404)
      expect(out.res.headersSent, 'no SSE header before the denial').to.equal(false)
      expect(out.res.headers['content-type']).to.equal(undefined)
      expect(out.res.eventsOf('RUN_ERROR')).to.have.length(0)
      expect(s.ai.streamCalls).to.deep.equal([])
      expect(s.store.writes).to.deep.equal([])
    })
  })

  const streamCases = [
    { name: 'addMessageStream', id: 'C3', body: { query: 'And Q4?', chatMode: 'internal_search' } },
    { name: 'regenerateAnswers', id: 'C11', body: { chatMode: 'quick' } },
  ]

  for (const c of streamCases) {
    for (const [who, user] of [['another user', stranger], ['a read-only recipient', recipient]] as const) {
      it(`${c.name}: ${who} is told the conversation was not found, and no answer is generated or saved${SUPERSEDED}`, async () => {
        const s = setup()

        // PH04-07: the guard denies before any SSE header, so the client gets a JSON 404 rather than a RUN_ERROR frame.
        const out = await call(env, c.id, user, s, { body: c.body })
        expect(out.error?.statusCode).to.equal(404)
        expect(out.res.body).to.equal('')
        expect(out.res.eventsOf('RUN_ERROR')).to.have.length(0)
        expect(s.ai.streamCalls).to.deep.equal([])
        expect(s.store.writes.filter((w) => w.startsWith('message.'))).to.deep.equal([])
        expect(s.store.messagesOf(s.chatId)).to.have.length(2)
        expect(s.store.messagesOf(s.agentChatId)).to.have.length(2)
      })
    }
  }
})

describe('es_controller deletion announces itself to the people who wrote in the conversation', () => {
  let f: Fixture
  afterEach(async () => {
    useConversationEventProducers(undefined)
    await f?.close()
    sinon.restore()
  })

  async function setupDeletion(kind: 'chat' | 'agent') {
    f = await startFixture()
    const session = f.store.session(f.ids[kind])!
    session.set('isShared', true)
    session.set('sharedWith', [
      { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
      { principalType: 'user', userId: PEOPLE.C, accessLevel: 'write' },
      { principalType: 'user', userId: PEOPLE.D, accessLevel: 'read' },
    ])
    for (const author of [PEOPLE.A, PEOPLE.B, PEOPLE.C, PEOPLE.B]) {
      f.store.addMessage(session, { messageType: 'user_query', content: 'q', authorUserId: author })
    }
    f.store.addMessage(session, { messageType: 'bot_response', content: 'a' })
    sinon.stub(ChatSessionMessage, 'distinct').callsFake((async (_field: string, filter: { messageType: string; sessionId: unknown }) => [
      ...new Set(f.store.messagesOf(filter.sessionId).filter((m) => m.messageType === filter.messageType && m.authorUserId).map((m) => String(m.authorUserId))),
    ].map((x) => new Types.ObjectId(x))) as never)
    useConversationEventProducers(f.env.container.get<IConversationEventProducers>(COLLAB_TYPES.ConversationEventProducers))
  }

  for (const kind of ['chat', 'agent'] as const) {
    it(`NT-05: the owner deletes a ${kind} with authors A, B, C and a viewer D: chat.deleted names B and C only`, async () => {
      await setupDeletion(kind)
      const out = await f.http.call('DELETE', f.path(kind), personAsUser('A'))
      expect(out.status).to.equal(200)
      const events = f.env.collaboration.notifier.events
      expect(events).to.have.length(1)
      expect(events[0]).to.deep.include({ type: 'chat.deleted', sessionId: f.ids[kind], actorUserId: personId('A') })
      expect([...(events[0] as { recipientUserIds: string[] }).recipientUserIds].sort()).to.deep.equal([personId('B'), personId('C')].sort())
    })
  }

  it('a failing announcement does not undo a delete on a standalone server', async () => {
    await setupDeletion('chat')
    f.env.collaboration.notifier.failWith = new Error('outbox down')
    const out = await f.http.call('DELETE', f.path('chat'), personAsUser('A'))
    expect(out.status).to.equal(200)
    expect(f.store.session(f.ids.chat)!.get('isDeleted')).to.equal(true)
  })

  it('with the flag off nothing is announced and no author lookup runs', async () => {
    f = await startFixture({ collab: false })
    const distinct = sinon.stub(ChatSessionMessage, 'distinct')
    useConversationEventProducers(f.env.container.get<IConversationEventProducers>(COLLAB_TYPES.ConversationEventProducers))
    const out = await f.http.call('DELETE', f.path('chat'), personAsUser('A'))
    expect(out.status).to.equal(200)
    expect(distinct.called).to.equal(false)
    expect(f.env.collaboration.notifier.events).to.have.length(0)
  })
})
