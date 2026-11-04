import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import * as artifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'

const CONTROLLER = '../../../../src/modules/enterprise_search/controller/es_controller'
const USER_ID = new mongoose.Types.ObjectId('aaaaaaaaaaaaaaaaaaaaaaaa')
const ORG_ID = new mongoose.Types.ObjectId('bbbbbbbbbbbbbbbbbbbbbbbb')

/** A session that refuses withTransaction inside a transaction, as the MongoDB driver does. */
function driverLikeSession() {
  const state = { started: false, committed: false }
  const session = {
    startTransaction: () => {
      state.started = true
    },
    withTransaction: async <T>(fn: () => Promise<T>): Promise<T> => {
      if (state.started) throw new Error('Transaction already in progress')
      const result = await fn()
      state.committed = true
      return result
    },
    commitTransaction: sinon.stub().resolves(),
    inTransaction: () => false,
    abortTransaction: sinon.stub().resolves(),
    endSession: sinon.stub(),
  }
  return { session, state }
}

describe('es_controller deleteConversationById on a replica set', () => {
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

  it('deletes the chat in one transaction instead of failing with "Transaction already in progress"', async () => {
    const { session, state } = driverLikeSession()
    sinon.stub(mongoose, 'startSession').resolves(session as never)
    sinon.stub(artifacts, 'deleteConversationArtifacts').resolves()
    const conversationId = new mongoose.Types.ObjectId().toString()
    sinon.stub(ChatSession, 'findOne').resolves({ _id: conversationId } as never)
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ _id: conversationId, updatedAt: new Date() } as never)
    sinon.stub(ChatSessionMessage, 'find').returns({ lean: () => Promise.resolve([]) } as never)

    const res = { status: sinon.stub().returnsThis(), json: sinon.stub() }
    const next = sinon.stub()
    await controller.deleteConversationById({} as never)(
      {
        params: { conversationId },
        user: { userId: USER_ID, orgId: ORG_ID },
        context: { requestId: 'r1' },
        headers: {},
      } as never,
      res as never,
      next,
    )

    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
    expect(state.committed).to.equal(true)
    expect(res.status.calledWith(200)).to.equal(true)
    expect(res.json.firstCall.args[0]).to.include({ id: conversationId, status: 'deleted' })
  })
})
