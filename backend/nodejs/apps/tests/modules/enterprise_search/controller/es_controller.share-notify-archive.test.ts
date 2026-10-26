import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * Two things a person sharing a chat relies on, for plain chats and agent chats alike:
 * the people it is shared with are told in their inbox, and archiving it takes it away from them.
 */

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OWNER = oid()
const RECIPIENT = oid()
const SECOND_RECIPIENT = oid()
const STRANGER = oid()
const AGENT_KEY = 'agent-1'

type Handler = (req: never, res: never, next: never) => Promise<unknown>

const request = (user: Types.ObjectId, params: Record<string, string>, body: Record<string, unknown> = {}): never =>
  ({
    headers: { authorization: 'Bearer token' },
    params,
    body,
    query: {},
    user: { userId: String(user), orgId: String(ORG), email: 'someone@example.com' },
    context: { requestId: 'req-share-notify-archive' },
  }) as never

async function call(handler: Handler, req: never) {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await handler(req, res as never, next as never)
  await settle()
  return {
    status: res.statusCode,
    body: res.jsonBody as Record<string, any> | undefined,
    error: next.firstCall?.args[0] as (Error & { statusCode?: number }) | undefined,
  }
}

interface Setup {
  store: InMemoryChatStore
  agentChatId: string
  plainChatId: string
}

let orgUsers: Set<string>
let createNotification: sinon.SinonStub

function setup(): Setup {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  store.stubNotifications()
  ai.install()

  // The share handler checks recipients with Users.find; this answers from orgUsers.
  ;(Users.find as unknown as sinon.SinonStub).callsFake((filter: Record<string, any>) => {
    const asked: string[] = ((filter._id?.$in ?? []) as unknown[]).map(String)
    const rows = asked.filter((id) => orgUsers.has(id)).map((id) => ({ _id: new Types.ObjectId(id) }))
    const query = { select: () => query, lean: () => query, exec: async () => rows }
    return query
  })
  createNotification = Notifications.create as unknown as sinon.SinonStub

  const agentChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Roadmap review',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    conversationSource: 'agent_chat',
  })
  store.addMessage(agentChat, { messageType: 'user_query', content: 'Summarise the roadmap' })
  const plainChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Plain chat',
    sessionType: 'chat',
  })
  store.addMessage(plainChat, { messageType: 'user_query', content: 'Hello' })
  return { store, agentChatId: String(agentChat._id), plainChatId: String(plainChat._id) }
}

const sharePlain = () => controller.shareConversationById(appConfig) as Handler
const shareAgent = () => controller.shareAgentConversation(appConfig) as Handler
const getPlain = () => controller.getConversationById(appConfig) as Handler
const getAgent = () => controller.getAgentConversationById(appConfig) as Handler
const agentParams = (s: Setup) => ({ conversationId: s.agentChatId, agentKey: AGENT_KEY })
const plainParams = (s: Setup) => ({ conversationId: s.plainChatId })

describe('es_controller: telling people a chat was shared with them, and archiving a shared chat', () => {
  beforeEach(() => {
    sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
    orgUsers = new Set([OWNER, RECIPIENT, SECOND_RECIPIENT, STRANGER].map(String))
  })
  afterEach(() => sinon.restore())

  describe('notification on share', () => {
    it('an agent chat: the recipient gets an unread inbox notification that opens the chat inside its agent', async () => {
      const s = setup()

      const out = await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))

      expect(out.status).to.equal(200)
      expect(createNotification.callCount).to.equal(1)
      const saved = createNotification.firstCall.args[0]
      expect(String(saved.assignedTo)).to.equal(String(RECIPIENT))
      expect(String(saved.orgId)).to.equal(String(ORG))
      expect(saved.status).to.equal('unread')
      expect(saved.type).to.equal('chatShared')
      expect(saved.title).to.equal('Owner Person shared an agent chat with you')
      expect(saved.message).to.contain('Roadmap review')
      expect(saved.redirectLink).to.equal(`/chat/?agentId=${AGENT_KEY}&conversationId=${s.agentChatId}`)
      expect(saved.payload).to.deep.include({
        conversationId: s.agentChatId,
        agentKey: AGENT_KEY,
        sharerName: 'Owner Person',
        chatTitle: 'Roadmap review',
      })
    })

    it('a plain chat: the recipient is told, and the link carries no agent', async () => {
      const s = setup()

      const out = await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))

      expect(out.status).to.equal(200)
      expect(createNotification.callCount).to.equal(1)
      const saved = createNotification.firstCall.args[0]
      expect(String(saved.assignedTo)).to.equal(String(RECIPIENT))
      expect(saved.title).to.equal('Owner Person shared a chat with you')
      expect(saved.redirectLink).to.equal(`/chat/?conversationId=${s.plainChatId}`)
      expect(saved.payload).to.not.have.property('agentKey')
      expect(saved.payload).to.deep.include({ sharerName: 'Owner Person', chatTitle: 'Plain chat' })
    })

    it('every new recipient is told once', async () => {
      const s = setup()

      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT), String(SECOND_RECIPIENT), String(RECIPIENT)] }))

      expect(createNotification.getCalls().map((c) => String(c.args[0].assignedTo))).to.have.members([
        String(RECIPIENT),
        String(SECOND_RECIPIENT),
      ])
    })

    it('sharing again with someone who already has access does not tell them again; only the new person is told', async () => {
      const s = setup()
      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))
      createNotification.resetHistory()

      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT), String(SECOND_RECIPIENT)] }))

      expect(createNotification.getCalls().map((c) => String(c.args[0].assignedTo))).to.deep.equal([String(SECOND_RECIPIENT)])
    })

    it('a share that is refused tells nobody', async () => {
      const s = setup()

      const notOwner = await call(sharePlain(), request(STRANGER, plainParams(s), { userIds: [String(RECIPIENT)] }))
      orgUsers.delete(String(RECIPIENT))
      const unknownUser = await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))
      const toSelf = await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(OWNER)] }))

      expect(notOwner.error?.statusCode).to.equal(404)
      expect(unknownUser.error?.statusCode).to.equal(400)
      expect(toSelf.error?.statusCode).to.equal(400)
      expect(createNotification.callCount).to.equal(0)
    })

    it('a notification that cannot be written does not fail the share', async () => {
      const s = setup()
      createNotification.rejects(new Error('mongo is down'))

      const out = await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))

      expect(out.status).to.equal(200)
      expect(out.error).to.equal(undefined)
      expect((out.body?.sharedWith as unknown[]).length).to.equal(1)
    })

    it('falls back to a neutral sharer name when the owner cannot be read', async () => {
      const s = setup()
      ;(Users.findOne as unknown as sinon.SinonStub).callsFake(() => {
        const query = { select: () => query, lean: () => query, exec: async () => null }
        return query
      })

      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))

      expect(createNotification.firstCall.args[0].title).to.equal('A colleague shared a chat with you')
    })
  })

  describe('archived by the owner', () => {
    const archiveAgent = (s: Setup) =>
      call(controller.archiveAgentConversation as unknown as Handler, request(OWNER, agentParams(s)))
    const archivePlain = (s: Setup) =>
      call(controller.archiveConversation as unknown as Handler, request(OWNER, plainParams(s)))

    it('an agent chat: once the owner archives it, the recipient gets "not found", as for any chat they cannot open', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      const before = await call(getAgent(), request(RECIPIENT, agentParams(s)))
      expect(before.error).to.equal(undefined)

      const archived = await archiveAgent(s)
      const after = await call(getAgent(), request(RECIPIENT, agentParams(s)))

      expect(archived.status).to.equal(200)
      expect(after.error?.statusCode).to.equal(404)
      expect(after.error?.message).to.equal('Conversation not found')
      expect(after.body).to.equal(undefined)
    })

    it('a plain chat: once the owner archives it, the recipient gets "not found", as for any chat they cannot open', async () => {
      const s = setup()
      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))
      const before = await call(getPlain(), request(RECIPIENT, plainParams(s)))
      expect(before.error).to.equal(undefined)

      const archived = await archivePlain(s)
      const after = await call(getPlain(), request(RECIPIENT, plainParams(s)))

      expect(archived.status).to.equal(200)
      expect(after.error?.statusCode).to.equal(404)
      expect(after.error?.message).to.equal('Conversation not found')
    })

    it('unarchiving gives the chat back to the recipient', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      await archiveAgent(s)
      s.store.session(s.agentChatId)?.set({ isArchived: false })

      const out = await call(getAgent(), request(RECIPIENT, agentParams(s)))

      expect(out.error).to.equal(undefined)
      expect(out.body?.conversation?.id ?? out.body?.conversation?._id).to.not.equal(undefined)
    })

    it('does not leave the shared-with-me list while archived', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      await archiveAgent(s)

      const list = await call(controller.getAllAgentConversations as unknown as Handler, request(RECIPIENT, { agentKey: AGENT_KEY }))

      expect(list.body?.sharedWithMeConversations).to.deep.equal([])
    })

    it('the owner can still open their own archived agent chat', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      await archiveAgent(s)

      const out = await call(getAgent(), request(OWNER, agentParams(s)))

      expect(out.error).to.equal(undefined)
    })

    it('a user it was never shared with gets the plain "not found", not the archived explanation', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      await archiveAgent(s)
      await call(sharePlain(), request(OWNER, plainParams(s), { userIds: [String(RECIPIENT)] }))
      await archivePlain(s)

      const agentOut = await call(getAgent(), request(STRANGER, agentParams(s)))
      const plainOut = await call(getPlain(), request(STRANGER, plainParams(s)))

      expect(agentOut.error?.statusCode).to.equal(404)
      expect(agentOut.error?.message).to.equal('Conversation not found')
      expect(plainOut.error?.statusCode).to.equal(404)
      expect(plainOut.error?.message).to.equal('Conversation not found')
    })

    it('a deleted chat still reads as "not found" to its recipient, not as archived', async () => {
      const s = setup()
      await call(shareAgent(), request(OWNER, agentParams(s), { userIds: [String(RECIPIENT)] }))
      s.store.session(s.agentChatId)?.set({ isArchived: true, isDeleted: true })

      const out = await call(getAgent(), request(RECIPIENT, agentParams(s)))

      expect(out.error?.message).to.equal('Conversation not found')
    })
  })
})
