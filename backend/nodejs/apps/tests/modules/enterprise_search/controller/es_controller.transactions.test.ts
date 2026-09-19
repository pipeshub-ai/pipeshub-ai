import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import * as searchUtils from '../../../../src/modules/enterprise_search/utils/utils'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { BadRequestError, InternalServerError } from '../../../../src/libs/errors/http.errors'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'

const CONTROLLER = '../../../../src/modules/enterprise_search/controller/es_controller'
const USER_ID = new mongoose.Types.ObjectId('aaaaaaaaaaaaaaaaaaaaaaaa')
const ORG_ID = new mongoose.Types.ObjectId('bbbbbbbbbbbbbbbbbbbbbbbb')
const CLASSIFIED = 'This conversation is too long for the selected model.'

/** A session whose transaction rolls back when the callback throws, like MongoDB's. */
function fakeTransactionSession() {
  const state = { committed: false, aborted: false }
  const session = {
    withTransaction: async <T>(fn: () => Promise<T>): Promise<T> => {
      try {
        const result = await fn()
        state.committed = true
        return result
      } catch (error) {
        state.aborted = true
        throw error
      }
    },
    inTransaction: () => false,
    abortTransaction: sinon.stub().resolves(),
    endSession: sinon.stub(),
  }
  return { session, state }
}

function conversationDoc(extra: Record<string, unknown> = {}) {
  const doc = {
    _id: new mongoose.Types.ObjectId(),
    orgId: ORG_ID,
    userId: USER_ID,
    title: 'hello',
    status: 'inProgress',
    save: sinon.stub(),
    toObject: sinon.stub().returns({}),
    ...extra,
  }
  doc.save.resolves(doc)
  return doc
}

function request(extra: Record<string, unknown> = {}) {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: { query: 'hello' },
    params: {},
    query: {},
    user: { userId: USER_ID, orgId: ORG_ID, email: 'test@test.com' },
    context: { requestId: 'req-1' },
    on: sinon.stub(),
    ...extra,
  }
}

const appConfig = {
  aiBackend: 'http://localhost:8000',
  connectorBackend: 'http://localhost:8088',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
  cmBackend: 'http://localhost:3001',
  iamBackend: 'http://localhost:3001',
  frontendUrl: 'http://localhost:3000',
}

describe('es_controller on a replica set: a failed answer is saved, not rolled back', () => {
  // The controller reads REPLICA_SET_AVAILABLE when it loads, so load a copy with it set.
  let controller: typeof import('../../../../src/modules/enterprise_search/controller/es_controller')
  const previous = process.env.REPLICA_SET_AVAILABLE

  before(() => {
    process.env.REPLICA_SET_AVAILABLE = 'true'
    delete require.cache[require.resolve(CONTROLLER)]
    controller = require(CONTROLLER)
  })

  after(() => {
    process.env.REPLICA_SET_AVAILABLE = previous
    delete require.cache[require.resolve(CONTROLLER)]
  })

  afterEach(() => sinon.restore())

  const cases = [
    { name: 'createConversation', handler: () => controller.createConversation(appConfig as never), mark: 'markConversationFailed' as const, req: request() },
    {
      name: 'createAgentConversation',
      handler: () => controller.createAgentConversation(appConfig as never),
      mark: 'markAgentConversationFailed' as const,
      req: request({ params: { agentKey: 'agent-1' } }),
    },
  ]

  function stubStores(session: unknown) {
    sinon.stub(mongoose, 'startSession').resolves(session as never)
    sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 1 } as never)
    sinon.stub(ChatSessionMessage, 'insertMany').resolves([
      { _id: new mongoose.Types.ObjectId(), toObject: () => ({}) },
    ] as never)
    sinon.stub(ChatSession.prototype, 'save').resolves(conversationDoc({ agentKey: 'agent-1' }))
  }

  for (const c of cases) {
    it(`${c.name}: an internal error reaches the client as the plain message, with no metadata`, async () => {
      stubStores(fakeTransactionSession().session)
      sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .rejects(new InternalServerError('Mongo write failed on shard rs0-2', { host: 'mongo-2.internal' }))
      sinon.stub(searchUtils, c.mark).resolves()
      const next = sinon.stub()

      await c.handler()(c.req as never, { status: sinon.stub().returnsThis(), json: sinon.stub() } as never, next)

      const sent = next.firstCall.args[0] as InternalServerError
      expect(sent.message).to.equal(CHAT_ERROR_MESSAGES.failed)
      expect(sent.metadata).to.be.undefined
      expect(JSON.stringify(sent.toJSON())).not.to.match(/shard|mongo-2/)
    })

    it(`${c.name}: a deliberate 4xx we raised keeps its status and message`, async () => {
      stubStores(fakeTransactionSession().session)
      sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new BadRequestError('Pick a model first.'))
      sinon.stub(searchUtils, c.mark).resolves()
      const next = sinon.stub()

      await c.handler()(c.req as never, { status: sinon.stub().returnsThis(), json: sinon.stub() } as never, next)

      const sent = next.firstCall.args[0] as BadRequestError
      expect(sent.statusCode).to.equal(400)
      expect(sent.message).to.equal('Pick a model first.')
    })

    it(`${c.name}: commits the failed state, then sends the error`, async () => {
      const { session, state } = fakeTransactionSession()
      sinon.stub(mongoose, 'startSession').resolves(session as never)
      // The user's question is appended to the message store before the AI is asked.
      sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 1 } as never)
      sinon.stub(ChatSessionMessage, 'insertMany').resolves([
        { _id: new mongoose.Types.ObjectId(), toObject: () => ({}) },
      ] as never)
      sinon.stub(ChatSession.prototype, 'save').resolves(conversationDoc({ agentKey: 'agent-1' }))
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: CLASSIFIED },
        msg: 'Bad Request',
      } as never)
      const markStub = sinon.stub(searchUtils, c.mark).resolves()
      const next = sinon.stub()

      await c.handler()(c.req as never, { status: sinon.stub().returnsThis(), json: sinon.stub() } as never, next)

      expect(markStub.calledOnce).to.be.true
      expect(markStub.firstCall.args[1]).to.equal(CLASSIFIED)
      expect(markStub.firstCall.args[2]).to.equal(session)
      expect(state.aborted, 'transaction rolled back the failed state').to.be.false
      expect(state.committed).to.be.true
      expect(next.calledOnce).to.be.true
      expect((next.firstCall.args[0] as Error).message).to.equal(CLASSIFIED)
    })
  }
})
