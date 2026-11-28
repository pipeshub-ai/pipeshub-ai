/**
 * Renaming a chat or an agent chat on a real MongoDB: the title and `rev` change, `updatedAt` does not, so a rename
 * neither reorders a list sorted by it nor reads as new activity. Skipped unless MONGO_IT_URI is set; each run uses its
 * own database and drops it afterwards.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeSSEResponse, settle } from './chat-test-harness'
import { behindGuard, realGuards } from '../helpers/guarded-chat'

const URI = process.env.MONGO_IT_URI
const describeIfMongo = URI ? describe : describe.skip
const oid = () => new mongoose.Types.ObjectId()

type Handler = (req: never, res: never, next: never) => Promise<unknown>

describeIfMongo('renaming a chat on a real MongoDB', function () {
  this.timeout(60000)
  const orgId = oid()
  const owner = oid()
  const AGENT_KEY = 'agent-1'
  const EARLIER = new Date('2026-11-01T10:00:00.000Z')

  const request = (params: Record<string, string>, title: string): never =>
    ({
      headers: { authorization: 'Bearer token' },
      params,
      body: { title },
      query: {},
      user: { userId: String(owner), orgId: String(orgId), email: 'someone@example.com' },
      context: { requestId: 'req-rename' },
    }) as never

  const call = async (handler: Handler, req: never) => {
    const res = new FakeSSEResponse()
    const next = sinon.stub()
    await handler(req, res as never, next as never)
    await settle()
    if (next.called) throw next.firstCall.args[0]
    return res
  }

  /** A chat as stored, last touched well before the rename. */
  const stored = async (fields: Record<string, unknown> = {}) => {
    const _id = oid()
    await ChatSession.collection.insertOne({
      _id,
      orgId,
      userId: owner,
      initiator: owner,
      title: 'Before',
      sessionType: 'chat',
      isDeleted: false,
      isShared: false,
      sharedWith: [],
      rev: 3,
      createdAt: EARLIER,
      updatedAt: EARLIER,
      lastActivityAt: EARLIER.getTime(),
      ...fields,
    } as never)
    return String(_id)
  }

  before(async () => {
    await mongoose.connect(URI as string, { dbName: `rename_test_${Date.now()}` })
    await Promise.all(Object.values(mongoose.models).map((model) => model.init()))
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  afterEach(() => sinon.restore())

  it('a chat keeps its updatedAt and activity; its title and rev change', async () => {
    const id = await stored()
    const rename = behindGuard(realGuards(), 'rename', controller.updateTitle as Handler, 'chat') as Handler

    const res = await call(rename, request({ conversationId: id }, 'After'))

    expect(res.statusCode).to.equal(200)
    const row = await ChatSession.collection.findOne({ _id: new mongoose.Types.ObjectId(id) })
    expect(row?.title).to.equal('After')
    expect(row?.rev).to.equal(4)
    expect(row?.updatedAt.toISOString()).to.equal(EARLIER.toISOString())
    expect(row?.lastActivityAt).to.equal(EARLIER.getTime())
  })

  it('an agent chat keeps its updatedAt too', async () => {
    const id = await stored({ sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat' })
    const rename = behindGuard(realGuards(), 'rename', controller.updateAgentConversationTitle as Handler, 'agent') as Handler

    const res = await call(rename, request({ conversationId: id, agentKey: AGENT_KEY }, 'After'))

    expect(res.statusCode).to.equal(200)
    const row = await ChatSession.collection.findOne({ _id: new mongoose.Types.ObjectId(id) })
    expect(row?.title).to.equal('After')
    expect(row?.rev).to.equal(4)
    expect(row?.updatedAt.toISOString()).to.equal(EARLIER.toISOString())
  })
})
