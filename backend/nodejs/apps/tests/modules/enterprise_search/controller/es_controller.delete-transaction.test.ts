import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { setConversationContext } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'

const CONTROLLER = '../../../../src/modules/enterprise_search/controller/es_controller'

/** Refuses what the driver refuses: `withTransaction` while a transaction is already open, and a commit with none. */
class StrictSession {
  open = false
  committed = 0
  startTransaction(): void {
    if (this.open) throw new Error('Transaction already in progress')
    this.open = true
  }
  async withTransaction<T>(fn: () => Promise<T>): Promise<T> {
    if (this.open) throw new Error('Transaction already in progress')
    this.open = true
    try {
      const result = await fn()
      this.open = false
      this.committed += 1
      return result
    } catch (error) {
      this.open = false
      throw error
    }
  }
  async commitTransaction(): Promise<void> {
    if (!this.open) throw new Error('No transaction started')
    this.open = false
    this.committed += 1
  }
  async abortTransaction(): Promise<void> {
    this.open = false
  }
  inTransaction(): boolean {
    return this.open
  }
  async endSession(): Promise<void> {}
}

describe('es_controller.deleteConversationById on a replica set', () => {
  let controller: typeof import('../../../../src/modules/enterprise_search/controller/es_controller')
  const previous = process.env.REPLICA_SET_AVAILABLE

  before(() => {
    process.env.REPLICA_SET_AVAILABLE = 'true'
    delete require.cache[require.resolve(CONTROLLER)]
    controller = require(CONTROLLER)
  })

  after(() => {
    if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
    else process.env.REPLICA_SET_AVAILABLE = previous
    delete require.cache[require.resolve(CONTROLLER)]
  })

  afterEach(() => sinon.restore())

  it('deletes inside one transaction that is committed once, and answers 200', async () => {
    const session = new StrictSession()
    sinon.stub(mongoose, 'startSession').resolves(session as never)
    const chatId = new mongoose.Types.ObjectId()
    const userId = new mongoose.Types.ObjectId()
    const orgId = new mongoose.Types.ObjectId()
    const chat = { _id: chatId, orgId, userId }
    sinon.stub(ChatSession, 'findOne').resolves(chat as never)
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ _id: chatId, updatedAt: new Date() } as never)
    sinon.stub(ChatSessionMessage, 'find').returns({ lean: () => Promise.resolve([{ citations: [{ citationId: new mongoose.Types.ObjectId() }] }]) } as never)
    const updateMany = sinon.stub(Citation, 'updateMany').resolves({} as never)

    const req: any = {
      params: { conversationId: String(chatId) },
      user: { userId, orgId },
      context: { requestId: 'r' },
      headers: {},
      body: {},
      query: {},
    }
    setConversationContext(req, {
      caller: { userId: String(userId), orgId: String(orgId), teamIds: [] },
      grant: { session: chat },
    } as never)
    const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
    const next = sinon.stub()

    await controller.deleteConversationById(req, res, next)

    expect(next.called, next.firstCall?.args[0]?.message).to.equal(false)
    expect(res.status.calledWith(200)).to.equal(true)
    expect(session.committed).to.equal(1)
    expect(session.open).to.equal(false)
    expect(updateMany.calledOnce).to.equal(true)
    expect(updateMany.firstCall.args[2]).to.deep.equal({ session })
  })
})
