/**
 * Share, notify and archive against a real MongoDB, so the archived filters and the inbox
 * documents are checked as the database evaluates them. Skipped unless SHARE_TEST_MONGO_URI is
 * set; each run uses its own database and drops it afterwards.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { FakeAIBackend, FakeSSEResponse, settle } from './chat-test-harness'

const URI = process.env.SHARE_TEST_MONGO_URI
const describeIfMongo = URI ? describe : describe.skip
const oid = () => new mongoose.Types.ObjectId()

const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test', jwtSecret: 's', scopedJwtSecret: 's' } as never
type Handler = (req: never, res: never, next: never) => Promise<unknown>

describeIfMongo('sharing a chat on a real MongoDB: the recipient is notified, and archiving takes it away', function () {
  this.timeout(60000)
  const orgId = oid()
  const owner = oid()
  const recipient = oid()
  const stranger = oid()
  const AGENT_KEY = 'agent-1'

  const request = (user: mongoose.Types.ObjectId, params: Record<string, string>, body: Record<string, unknown> = {}): never =>
    ({
      headers: { authorization: 'Bearer token' },
      params,
      body,
      query: {},
      user: { userId: String(user), orgId: String(orgId), email: 'someone@example.com' },
      context: { requestId: 'req-mongo' },
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

  before(async () => {
    await mongoose.connect(URI as string, { dbName: `share_test_${Date.now()}` })
    await Users.collection.insertMany([
      { _id: owner, orgId, fullName: 'Olivia Owner', email: 'o@example.com', isDeleted: false },
      { _id: recipient, orgId, fullName: 'Riley Recipient', email: 'r@example.com', isDeleted: false },
      { _id: stranger, orgId, fullName: 'Sam Stranger', email: 's@example.com', isDeleted: false },
    ] as never)
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  beforeEach(() => {
    sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
    new FakeAIBackend().install()
  })
  afterEach(() => sinon.restore())

  const seed = async (kind: 'agent' | 'plain') => {
    const session = await ChatSession.create({
      sessionType: kind === 'agent' ? 'agent' : 'chat',
      ...(kind === 'agent' ? { agentKey: AGENT_KEY, conversationSource: 'agent_chat' } : {}),
      userId: owner,
      initiator: owner,
      orgId,
      title: kind === 'agent' ? 'Roadmap review' : 'Plain chat',
    })
    await ChatSessionMessage.create({ sessionId: session._id, orgId, seq: 1, messageType: 'user_query', content: 'Hello' })
    return String(session._id)
  }

  for (const kind of ['agent', 'plain'] as const) {
    describe(`${kind} chat`, () => {
      const params = (id: string) => (kind === 'agent' ? { conversationId: id, agentKey: AGENT_KEY } : { conversationId: id })
      const share = (id: string, ...users: mongoose.Types.ObjectId[]) =>
        call(
          (kind === 'agent' ? controller.shareAgentConversation(appConfig) : controller.shareConversationById(appConfig)) as Handler,
          request(owner, params(id), { userIds: users.map(String) }),
        )
      const open = (id: string, user: mongoose.Types.ObjectId) =>
        call(
          (kind === 'agent' ? controller.getAgentConversationById(appConfig) : controller.getConversationById(appConfig)) as Handler,
          request(user, params(id)),
        )
      const archive = (id: string) =>
        call((kind === 'agent' ? controller.archiveAgentConversation : controller.archiveConversation) as unknown as Handler, request(owner, params(id)))
      const unarchive = (id: string) =>
        call((kind === 'agent' ? controller.unarchiveAgentConversation : controller.unarchiveConversation) as unknown as Handler, request(owner, params(id)))

      it('stores one unread notification for the recipient that links to the chat, and none for a repeat share', async () => {
        const id = await seed(kind)

        const first = await share(id, recipient)
        const repeat = await share(id, recipient)

        expect(first.status).to.equal(200)
        expect(repeat.status).to.equal(200)
        const rows = await Notifications.find({ assignedTo: recipient, 'payload.conversationId': id }).lean()
        expect(rows).to.have.length(1)
        const row = rows[0] as Record<string, any>
        expect(String(row.orgId)).to.equal(String(orgId))
        expect(row.status).to.equal('unread')
        expect(row.isDeleted).to.equal(false)
        expect(row.payload).to.deep.include({ sharerName: 'Olivia Owner' })
        expect(row.title).to.equal(kind === 'agent' ? 'Olivia Owner shared an agent chat with you' : 'Olivia Owner shared a chat with you')
        expect(row.redirectLink).to.equal(kind === 'agent' ? `/chat/?agentId=${AGENT_KEY}&conversationId=${id}` : `/chat/?conversationId=${id}`)
        expect(await Notifications.countDocuments({ assignedTo: owner })).to.equal(0)
      })

      it('the recipient reads it, loses it when the owner archives it, and gets it back on unarchive', async () => {
        const id = await seed(kind)
        await share(id, recipient)
        expect((await open(id, recipient)).error).to.equal(undefined)

        expect((await archive(id)).status).to.equal(200)
        const hidden = await open(id, recipient)
        const ownerStill = await open(id, owner)

        expect(hidden.error?.statusCode).to.equal(404)
        expect(hidden.error?.message).to.equal('Conversation not found')
        if (kind === 'agent') expect(ownerStill.error).to.equal(undefined)

        expect((await unarchive(id)).status).to.equal(200)
        expect((await open(id, recipient)).error).to.equal(undefined)
      })

      it('someone it was never shared with never learns that an archived chat exists', async () => {
        const id = await seed(kind)
        await share(id, recipient)
        await archive(id)

        const out = await open(id, stranger)

        expect(out.error?.statusCode).to.equal(404)
        expect(out.error?.message).to.equal('Conversation not found')
      })
    })
  }

  it('an archived agent chat leaves the shared-with-me list of its recipient', async () => {
    const id = await seed('agent')
    await call(controller.shareAgentConversation(appConfig) as Handler, request(owner, { conversationId: id, agentKey: AGENT_KEY }, { userIds: [String(recipient)] }))
    const list = () => call(controller.getAllAgentConversations as unknown as Handler, request(recipient, { agentKey: AGENT_KEY }))
    expect(((await list()).body?.sharedWithMeConversations as unknown[]).length).to.equal(1)

    await call(controller.archiveAgentConversation as unknown as Handler, request(owner, { conversationId: id, agentKey: AGENT_KEY }))

    expect(((await list()).body?.sharedWithMeConversations as unknown[]).length).to.equal(0)
  })
})
