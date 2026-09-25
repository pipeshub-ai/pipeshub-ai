import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import type { Response } from 'express'
import {
  handleRegenerationError,
  handleRegenerationStreamData,
  handleRegenerationSuccess,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { AGUI_PROTOCOL, frameAGUI } from '../../../../src/modules/enterprise_search/utils/agui'
import { StreamedContentAccumulator } from '../../../../src/modules/enterprise_search/utils/stream-lifecycle'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'
import { CONVERSATION_STATUS } from '../../../../src/modules/enterprise_search/constants/constants'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import type {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
} from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

interface ConversationError {
  message: string
  errorType: string
  messageId?: mongoose.Types.ObjectId
  stack?: string
}

interface FakeConversation {
  _id: mongoose.Types.ObjectId
  orgId: mongoose.Types.ObjectId
  status: string
  failReason?: string
  lastActivityAt?: number
  agentKey?: string
  modelInfo: Partial<IAIModel>
  conversationErrors: ConversationError[]
  save: sinon.SinonStub
  toObject: () => Record<string, unknown>
}

interface FakeResponse {
  write: sinon.SinonStub
  flush: sinon.SinonStub
  writeHead: sinon.SinonStub
}

interface ReplacedMessage {
  messageType: string
  content: string
  sessionId: mongoose.Types.ObjectId
  orgId: mongoose.Types.ObjectId
  seq: number
  status?: string
  citations?: Array<{ citationId: mongoose.Types.ObjectId }>
}

const makeConversation = (overrides: Partial<FakeConversation> = {}): FakeConversation => {
  const conversation: FakeConversation = {
    _id: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(),
    status: CONVERSATION_STATUS.INPROGRESS,
    modelInfo: { modelKey: 'old-key', modelName: 'old-model', modelProvider: 'old', chatMode: 'quick' },
    conversationErrors: [],
    save: sinon.stub(),
    toObject: () => ({ _id: conversation._id, title: 'Quarterly numbers', status: conversation.status }),
    ...overrides,
  }
  if (!overrides.save) conversation.save.resolves(conversation)
  return conversation
}

const asDoc = (conversation: FakeConversation): IChatSessionDocument =>
  conversation as unknown as IChatSessionDocument

const at = <T>(items: readonly T[], index = 0): T => {
  const item = items[index]
  if (item === undefined) throw new Error(`expected an element at index ${String(index)}`)
  return item
}

const makeRes = (): FakeResponse => ({
  write: sinon.stub().returns(true),
  flush: sinon.stub(),
  writeHead: sinon.stub(),
})

const asRes = (res: FakeResponse): Response => res as unknown as Response

const written = (res: FakeResponse): string =>
  res.write.getCalls().map((call) => String(call.args[0])).join('')

/** Lets the fire-and-forget persistence inside the stream handler settle. */
const settle = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve()
  await new Promise((resolve) => setImmediate(resolve))
}

const completeAnswer = (overrides: Partial<IAIResponse> = {}): IAIResponse => ({
  answer: 'Revenue grew 12% quarter on quarter.',
  citations: [],
  reason: '',
  answerMatchType: 'Exact Match',
  documentIndexes: [],
  ...overrides,
} as IAIResponse)

interface StreamCall {
  chunk: string
  buffer?: string
  conversation?: FakeConversation | null
  messageId?: mongoose.Types.ObjectId | string | null
  onComplete?: (data: IAIResponse) => void
  isAgentSession?: boolean
  accumulator?: StreamedContentAccumulator
}

const feed = (res: FakeResponse, call: StreamCall): string =>
  handleRegenerationStreamData(
    Buffer.from(call.chunk),
    call.buffer ?? '',
    call.conversation ? asDoc(call.conversation) : null,
    call.messageId ?? null,
    null,
    'req-regen',
    asRes(res),
    call.onComplete ?? ((): void => undefined),
    call.isAgentSession ?? false,
    AGUI_PROTOCOL,
    call.accumulator,
  )

describe('Regenerating an answer (enterprise search utils)', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('handleRegenerationStreamData on the AG-UI stream', () => {
    it('holds back a frame split across chunks and forwards it whole once it completes', () => {
      const res = makeRes()
      const frame = frameAGUI('TEXT_MESSAGE_CONTENT', { runId: 'run-1', delta: 'Revenue grew' })
      const cut = Math.floor(frame.length / 2)

      const carried = feed(res, { chunk: frame.slice(0, cut) })

      expect(res.write.called).to.equal(false)
      expect(carried).to.equal(frame.slice(0, cut))

      const rest = feed(res, { chunk: frame.slice(cut), buffer: carried })

      expect(rest).to.equal('')
      expect(res.write.calledOnce).to.equal(true)
      expect(written(res)).to.equal(frame)
      expect(res.flush.calledOnce).to.equal(true)
    })

    it('forwards text deltas and remembers only the root run for a partial save', () => {
      const res = makeRes()
      const accumulator = new StreamedContentAccumulator()
      const chunk =
        frameAGUI('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Revenue ' }) +
        frameAGUI('TEXT_MESSAGE_CONTENT', { runId: 'sub', parentRunId: 'root', delta: '[sub-agent notes]' }) +
        frameAGUI('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'grew 12%.' })

      feed(res, { chunk, accumulator })

      expect(accumulator.getText()).to.equal('Revenue grew 12%.')
      expect(written(res)).to.equal(chunk)
    })

    it('still forwards a text delta whose data is not valid JSON', () => {
      const res = makeRes()
      const accumulator = new StreamedContentAccumulator()
      const chunk = 'event: TEXT_MESSAGE_CONTENT\ndata: {not json\n\n'

      feed(res, { chunk, accumulator })

      expect(written(res)).to.equal(chunk)
      expect(accumulator.hasContent()).to.equal(false)
    })

    it('hands RUN_FINISHED to the caller instead of the client, but keeps the frames around it', () => {
      const res = makeRes()
      const onComplete = sinon.stub()
      const result = completeAnswer()
      const before = frameAGUI('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Revenue grew 12%.' })
      const after = frameAGUI('STATE_SNAPSHOT', { snapshot: { step: 'done' } })

      feed(res, { chunk: before + frameAGUI('RUN_FINISHED', { result }) + after, onComplete })

      expect(onComplete.calledOnceWithExactly(result)).to.equal(true)
      expect(written(res)).to.equal(before + after)
      expect(written(res)).to.not.include('RUN_FINISHED')
    })

    it('replaces the regenerated message with the error Python reported and forwards RUN_ERROR', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      const existing = { _id: messageId, sessionId: conversation._id, orgId: conversation.orgId, seq: 4 }
      sinon.stub(ChatSessionMessage, 'findById').resolves(existing)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves(existing)
      const frame = frameAGUI('RUN_ERROR', { message: 'The AI model is not configured.', code: 'llm_config' })

      feed(res, { chunk: frame, conversation, messageId })
      await settle()

      expect(written(res)).to.equal(frame)
      expect(replace.calledOnce).to.equal(true)
      const [filter, replacement] = replace.firstCall.args as [{ _id: mongoose.Types.ObjectId }, ReplacedMessage]
      expect(filter._id).to.equal(messageId)
      expect(replacement.messageType).to.equal('error')
      expect(replacement.content).to.equal('The AI model is not configured.')
      expect(replacement.seq).to.equal(4)
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(conversation.failReason).to.equal('The AI model is not configured.')
      expect(at(conversation.conversationErrors).messageId?.toString()).to.equal(messageId.toString())
      expect(conversation.save.calledOnce).to.equal(true)
    })

    it('saves the standard failure text when RUN_ERROR carries no message', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ _id: messageId })

      feed(res, { chunk: frameAGUI('RUN_ERROR', { code: 'boom' }), conversation, messageId })
      await settle()

      const replacement = replace.firstCall.args[1] as ReplacedMessage
      expect(replacement.content).to.equal(CHAT_ERROR_MESSAGES.failed)
      expect(conversation.failReason).to.equal(CHAT_ERROR_MESSAGES.failed)
    })

    it('keeps streaming when saving the RUN_ERROR fails', async () => {
      const res = makeRes()
      const conversation = makeConversation({ save: sinon.stub().rejects(new Error('mongo down')) })
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ _id: messageId })
      const frame = frameAGUI('RUN_ERROR', { message: 'Rate limited' })
      const unhandled = sinon.spy()
      process.on('unhandledRejection', unhandled)

      try {
        expect(() => feed(res, { chunk: frame, conversation, messageId })).to.not.throw()
        await settle()
      } finally {
        process.removeListener('unhandledRejection', unhandled)
      }

      expect(written(res)).to.equal(frame)
      expect(unhandled.called).to.equal(false)
    })

    it('does not touch any message when RUN_ERROR arrives without a message to replace', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const findById = sinon.stub(ChatSessionMessage, 'findById')
      const frame = frameAGUI('RUN_ERROR', { message: 'nope' })

      feed(res, { chunk: frame, conversation, messageId: null })
      await settle()

      expect(written(res)).to.equal(frame)
      expect(findById.called).to.equal(false)
      expect(conversation.save.called).to.equal(false)
    })

    it('records an ask_user_question for an agent regeneration and forwards it', async () => {
      const res = makeRes()
      const conversation = makeConversation({ agentKey: 'agent-1' })
      sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 9 })
      const insert = sinon.stub(ChatSessionMessage, 'insertMany').resolves([])
      const toolData = { question: 'Which region?', options: ['EMEA', 'APAC'] }
      const frame = frameAGUI('CUSTOM', { name: 'ask_user_question', value: { toolData } })

      feed(res, { chunk: frame, conversation, isAgentSession: true })
      await settle()

      expect(written(res)).to.equal(frame)
      const [docs] = insert.firstCall.args as [Array<{ sessionId: mongoose.Types.ObjectId; orgId: mongoose.Types.ObjectId; seq: number; tools: Array<{ toolResult: unknown }> }>]
      expect(at(docs).sessionId).to.equal(conversation._id)
      expect(at(docs).orgId).to.equal(conversation.orgId)
      expect(at(docs).seq).to.equal(9)
      expect(at(at(docs).tools).toolResult).to.deep.equal(toolData)
    })

    it('forwards ask_user_question without saving it for a plain chat regeneration', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const allocate = sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq: 1 })
      const insert = sinon.stub(ChatSessionMessage, 'insertMany').resolves([])
      const frame = frameAGUI('CUSTOM', { name: 'ask_user_question', value: { toolData: { q: 1 } } })

      feed(res, { chunk: frame, conversation, isAgentSession: false })
      await settle()

      expect(written(res)).to.equal(frame)
      expect(allocate.called).to.equal(false)
      expect(insert.called).to.equal(false)
    })

    it('keeps streaming when saving an ask_user_question fails', async () => {
      const res = makeRes()
      const conversation = makeConversation({ agentKey: 'agent-1' })
      sinon.stub(ChatSession, 'findOneAndUpdate').resolves(null)
      const insert = sinon.stub(ChatSessionMessage, 'insertMany')
      const frame = frameAGUI('CUSTOM', { name: 'ask_user_question', value: { toolData: {} } })
      const unhandled = sinon.spy()
      process.on('unhandledRejection', unhandled)

      try {
        feed(res, { chunk: frame, conversation, isAgentSession: true })
        await settle()
      } finally {
        process.removeListener('unhandledRejection', unhandled)
      }

      expect(written(res)).to.equal(frame)
      expect(insert.called).to.equal(false)
      expect(unhandled.called).to.equal(false)
    })

    it('forwards a CUSTOM frame with unreadable data unchanged', () => {
      const res = makeRes()
      const chunk = 'event: CUSTOM\ndata: {broken\n\n'

      feed(res, { chunk, conversation: makeConversation(), isAgentSession: true })

      expect(written(res)).to.equal(chunk)
    })

    it('forwards CUSTOM frames untouched when there is no conversation to save to', () => {
      const res = makeRes()
      const insert = sinon.stub(ChatSessionMessage, 'insertMany')
      const frame = frameAGUI('CUSTOM', { name: 'ask_user_question', value: { toolData: {} } })

      feed(res, { chunk: frame, conversation: null, isAgentSession: true })

      expect(written(res)).to.equal(frame)
      expect(insert.called).to.equal(false)
    })
  })

  describe('handleRegenerationSuccess', () => {
    const stubCitationSave = (): sinon.SinonStub =>
      sinon.stub(Citation.prototype, 'save').callsFake(function (this: unknown) {
        return Promise.resolve(this)
      })

    it('replaces the original message in place and stamps new citations with the caller org', async () => {
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      const existing = { _id: messageId, sessionId: conversation._id, orgId: conversation.orgId, seq: 6 }
      sinon.stub(ChatSessionMessage, 'findById').resolves(existing)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').callsFake(
        (_filter: unknown, replacement: unknown) =>
          Promise.resolve({ toObject: () => ({ _id: messageId, ...(replacement as object) }) }) as never,
      )
      const citationSave = stubCitationSave()
      const orgId = new mongoose.Types.ObjectId().toString()
      const data = completeAnswer({
        citations: [
          {
            content: 'Q3 revenue: $12M',
            chunkIndex: 2,
            citationType: 'vectordb|document',
            metadata: { recordId: 'rec-1', orgId: 'spoofed-org' },
          },
        ] as unknown as IAIResponse['citations'],
      })
      const modelInfo: IAIModel = {
        modelKey: 'new-key',
        modelName: 'gpt-x',
        modelProvider: 'openai',
        chatMode: 'deep',
      }

      const outcome = await handleRegenerationSuccess(
        data,
        asDoc(conversation),
        messageId,
        orgId,
        null,
        modelInfo,
      )
      const response: unknown = outcome.conversation
      const { savedCitations } = outcome

      expect(citationSave.calledOnce).to.equal(true)
      expect(savedCitations).to.have.length(1)
      const saved = savedCitations[0] as unknown as { metadata: { orgId: string; recordId: string } }
      expect(saved.metadata.orgId).to.equal(orgId)
      expect(saved.metadata.recordId).to.equal('rec-1')

      const [filter, replacement] = replace.firstCall.args as [{ _id: mongoose.Types.ObjectId }, ReplacedMessage]
      expect(filter._id).to.equal(messageId)
      expect(replacement.content).to.equal('Revenue grew 12% quarter on quarter.')
      expect(replacement.messageType).to.equal('bot_response')
      expect(replacement.seq).to.equal(6)
      expect(replacement.sessionId).to.equal(conversation._id)
      expect(at(replacement.citations ?? []).citationId.toString()).to.equal(
        (savedCitations[0] as unknown as { _id: mongoose.Types.ObjectId })._id.toString(),
      )

      expect(conversation.status).to.equal(CONVERSATION_STATUS.COMPLETE)
      expect(conversation.modelInfo).to.include({ modelKey: 'new-key', modelName: 'gpt-x', chatMode: 'deep' })
      expect(conversation.save.calledOnce).to.equal(true)

      const body = response as { title: string; messages: Array<{ content: string; citations: Array<{ citationData?: unknown }> }> }
      expect(body.title).to.equal('Quarterly numbers')
      expect(body.messages).to.have.length(1)
      expect(at(body.messages).content).to.equal('Revenue grew 12% quarter on quarter.')
      expect(at(at(body.messages).citations).citationData).to.equal(savedCitations[0])
    })

    it('passes the transaction to citation and conversation saves when one is given', async () => {
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      const mongoSession = { id: 'txn' } as unknown as mongoose.ClientSession
      const findById = sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ toObject: () => ({ _id: messageId }) })
      const citationSave = stubCitationSave()

      await handleRegenerationSuccess(
        completeAnswer({ citations: [{ content: 'x', citationType: 'vectordb|document', metadata: {} }] as unknown as IAIResponse['citations'] }),
        asDoc(conversation),
        messageId.toString(),
        new mongoose.Types.ObjectId().toString(),
        mongoSession,
      )

      expect(citationSave.firstCall.args[0]).to.deep.equal({ session: mongoSession })
      expect(conversation.save.firstCall.args[0]).to.deep.equal({ session: mongoSession })
      expect(((findById.firstCall.args as unknown[])[2] as { session: unknown }).session).to.equal(mongoSession)
    })

    it('fails without saving the conversation when the message to replace is gone', async () => {
      const conversation = makeConversation()
      sinon.stub(ChatSessionMessage, 'findById').resolves(null)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace')

      let caught: unknown
      try {
        await handleRegenerationSuccess(completeAnswer(), asDoc(conversation), new mongoose.Types.ObjectId(), 'org', null)
      } catch (error) {
        caught = error
      }

      expect(caught).to.be.instanceOf(InternalServerError)
      expect(replace.called).to.equal(false)
      expect(conversation.save.called).to.equal(false)
    })

    it('fails when the conversation cannot be saved', async () => {
      const conversation = makeConversation({ save: sinon.stub().resolves(null) })
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ toObject: () => ({}) })

      let caught: unknown
      try {
        await handleRegenerationSuccess(completeAnswer(), asDoc(conversation), messageId, 'org', null)
      } catch (error) {
        caught = error
      }

      expect(caught).to.be.instanceOf(InternalServerError)
      expect((caught as Error).message).to.equal('Failed to update conversation with regenerated response')
    })

    it('marks the conversation failed when the regenerated answer is a classified failure', async () => {
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ toObject: () => ({}) })
      const failure = completeAnswer({
        answer: 'Your AI provider rejected the API key. Ask your admin to update it.',
        errorCode: 'auth_error',
      })

      await handleRegenerationSuccess(failure, asDoc(conversation), messageId, 'org', null)

      expect((replace.firstCall.args[1] as ReplacedMessage).messageType).to.equal('error')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(conversation.failReason).to.equal(failure.answer)
      expect(at(conversation.conversationErrors).errorType).to.equal('auth_error')
    })

    it('keeps a stopped regeneration as a stopped answer', async () => {
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 1 })
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ toObject: () => ({}) })

      await handleRegenerationSuccess(
        completeAnswer({ answer: '', status: 'stopped' }),
        asDoc(conversation),
        messageId,
        'org',
        null,
      )

      const replacement = replace.firstCall.args[1] as ReplacedMessage
      expect(replacement.status).to.equal('stopped')
      expect(replacement.content).to.equal('')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.STOPPED)
    })
  })

  describe('handleRegenerationError', () => {
    const stubMessagesQuery = (messages: unknown[]): sinon.SinonStub => {
      const chain = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(messages),
      }
      return sinon.stub(ChatSessionMessage, 'find').returns(chain as never)
    }

    it('shows the plain "unavailable" text and the refreshed conversation, never the raw error', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 2 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ _id: messageId })
      const reloaded = { _id: conversation._id, toObject: () => ({ _id: conversation._id, title: 'Reloaded', nextSeq: 3 }) }
      const findConversation = sinon.stub(ChatSession, 'findById').resolves(reloaded)
      stubMessagesQuery([{ _id: messageId, messageType: 'error', content: CHAT_ERROR_MESSAGES.unavailable, seq: 2, orgId: 'o' }])
      const error = Object.assign(new Error('connect ECONNREFUSED 10.0.0.7:8000'), { code: 'ECONNREFUSED' })

      await handleRegenerationError(
        asRes(res), error, asDoc(conversation), messageId, conversation._id.toString(), null, 'req-1', 'regeneration_error', AGUI_PROTOCOL,
      )

      expect(findConversation.calledOnceWithExactly(conversation._id.toString())).to.equal(true)
      const out = written(res)
      expect(out.startsWith('event: RUN_ERROR\n')).to.equal(true)
      const payload = JSON.parse(at(out.split('data: '), 1)) as { message: string; conversation: { title: string; nextSeq?: number; messages: Array<{ content: string; seq?: number }> } }
      expect(payload.message).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(payload.conversation.title).to.equal('Reloaded')
      expect(payload.conversation).to.not.have.property('nextSeq')
      expect(at(payload.conversation.messages).content).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(payload.conversation.messages[0]).to.not.have.property('seq')
      expect(out).to.not.include('10.0.0.7')
      expect(out).to.not.include('ECONNREFUSED')
    })

    it('still tells the user when the conversation cannot be reloaded', async () => {
      const res = makeRes()
      const conversation = makeConversation()
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 2 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ _id: messageId })
      sinon.stub(ChatSession, 'findById').resolves(null)

      await handleRegenerationError(
        asRes(res), new Error('socket hang up'), asDoc(conversation), messageId, 'conv-1', null, 'req-1', 'regeneration_error', AGUI_PROTOCOL,
      )

      const payload = JSON.parse(at(written(res).split('data: '), 1)) as { message: string; conversation?: unknown }
      expect(payload.message).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      expect(payload).to.not.have.property('conversation')
    })

    it('still tells the user when saving the error itself fails', async () => {
      const res = makeRes()
      const conversation = makeConversation({ save: sinon.stub().rejects(new Error('write conflict')) })
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ _id: messageId, seq: 2 })
      sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ _id: messageId })
      const reload = sinon.stub(ChatSession, 'findById')

      await handleRegenerationError(
        asRes(res), new Error('stack overflow in parser at line 7'), asDoc(conversation), messageId, 'conv-1', null, 'req-1', 'regeneration_error', AGUI_PROTOCOL,
      )

      expect(reload.called).to.equal(false)
      const out = written(res)
      const payload = JSON.parse(at(out.split('data: '), 1)) as { message: string; conversation?: unknown }
      expect(payload.message).to.equal(CHAT_ERROR_MESSAGES.failed)
      expect(payload).to.not.have.property('conversation')
      expect(out).to.not.include('parser')
      expect(out).to.not.include('write conflict')
    })
  })
})
