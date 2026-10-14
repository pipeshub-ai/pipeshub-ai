import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import {
  allocateSeq,
  appendMessages,
  attachSharedBy,
  attachSharedByIfRecipient,
  formatPreviousConversations,
  keepRunAlive,
  markAgentConversationFailed,
  markConversationFailed,
  releaseRun,
  reserveRun,
  replaceMessageWithError,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  savePartialConversation,
  staleAskUserQuestionToolCallIds,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { PassThrough } from 'stream'
import { InMemoryChatStore } from '../controller/chat-test-harness'
import { ConflictError, InternalServerError, NotFoundError } from '../../../../src/libs/errors/http.errors'
import { CONVERSATION_STATUS } from '../../../../src/modules/enterprise_search/constants/constants'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import type {
  IAIModel,
  IAIResponse,
  IChatSessionDocument,
  IMessage,
} from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

interface FakeConversation {
  _id: mongoose.Types.ObjectId
  orgId: mongoose.Types.ObjectId
  status: string
  failReason?: string
  agentKey?: string
  modelInfo: Partial<IAIModel>
  conversationErrors: Array<{ message: string; errorType: string; stack?: string }>
  save: sinon.SinonStub
  toObject: () => Record<string, unknown>
}

interface InsertedMessage {
  sessionId: mongoose.Types.ObjectId
  orgId: mongoose.Types.ObjectId
  seq: number
  messageType: string
  content: string
  status?: string
  citations?: Array<{ citationId: mongoose.Types.ObjectId }>
}

const makeConversation = (overrides: Partial<FakeConversation> = {}): FakeConversation => {
  const conversation: FakeConversation = {
    _id: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(),
    status: CONVERSATION_STATUS.INPROGRESS,
    modelInfo: { modelKey: 'k', modelName: 'm', modelProvider: 'p', chatMode: 'quick' },
    conversationErrors: [],
    save: sinon.stub(),
    toObject: () => ({ _id: conversation._id, title: 'Roadmap' }),
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

/** allocateSeq's `$inc` then insertMany, echoing the inserted rows back as documents. */
const stubAppend = (nextSeq: number | null): { insert: sinon.SinonStub; allocate: sinon.SinonStub } => {
  const allocate = sinon.stub(ChatSession, 'findOneAndUpdate').resolves(nextSeq === null ? null : { nextSeq })
  const insert = sinon.stub(ChatSessionMessage, 'insertMany').callsFake(
    (docs: unknown) =>
      Promise.resolve(
        (docs as InsertedMessage[]).map((doc) => ({ ...doc, toObject: () => ({ _id: new mongoose.Types.ObjectId(), ...doc }) })),
      ) as never,
  )
  return { insert, allocate }
}

const answer = (overrides: Partial<IAIResponse> = {}): IAIResponse => ({
  answer: 'Ship the connector in Q4.',
  citations: [],
  reason: '',
  answerMatchType: 'Exact Match',
  documentIndexes: [],
  ...overrides,
} as IAIResponse)

const oneCitation = (): IAIResponse['citations'] =>
  [
    { content: 'Q4 plan', chunkIndex: 0, citationType: 'vectordb|document', metadata: { orgId: 'someone-else', recordId: 'r1' } },
  ] as unknown as IAIResponse['citations']

const stubCitationSave = (): sinon.SinonStub =>
  sinon.stub(Citation.prototype, 'save').callsFake(function (this: unknown) {
    return Promise.resolve(this)
  })

const rejection = async (promise: Promise<unknown>): Promise<unknown> => {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the promise to reject')
}

describe('Saving chat answers (enterprise search utils)', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('allocateSeq / appendMessages', () => {
    it('refuses to allocate a sequence for a session that no longer exists, and inserts nothing', async () => {
      const { insert } = stubAppend(null)

      const error = await rejection(
        appendMessages(new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId(), [
          { messageType: 'bot_response', content: 'hi' } as IMessage,
        ]),
      )

      expect(error).to.be.instanceOf(NotFoundError)
      expect(insert.called).to.equal(false)
    })

    it('allocates against the given session id only', async () => {
      const sessionId = new mongoose.Types.ObjectId()
      const { allocate } = stubAppend(3)

      expect(await allocateSeq(sessionId, 3)).to.equal(3)
      const [filter, update] = allocate.firstCall.args as [{ _id: mongoose.Types.ObjectId }, { $inc: { nextSeq: number } }]
      expect(filter).to.deep.equal({ _id: sessionId })
      expect(update.$inc.nextSeq).to.equal(3)
    })
  })

  for (const [label, save, failText] of [
    ['saveCompleteConversation', saveCompleteConversation, 'Failed to update conversation'],
    ['saveCompleteAgentConversation', saveCompleteAgentConversation, 'Failed to update agent conversation'],
  ] as const) {
    describe(label, () => {
      it('saves citations under the caller org and appends the answer after the last message', async () => {
        const conversation = makeConversation({ agentKey: 'agent-7' })
        const { insert } = stubAppend(12)
        const citationSave = stubCitationSave()
        const orgId = new mongoose.Types.ObjectId().toString()
        const mongoSession = { id: 'txn' } as unknown as mongoose.ClientSession

        const response = (await save(
          asDoc(conversation),
          answer({ citations: oneCitation() }),
          orgId,
          mongoSession,
          { modelKey: 'k2', modelName: 'm2', modelProvider: 'p2', chatMode: 'deep', modelFriendlyName: 'Model Two' },
        )) as { title: string; messages: Array<{ content: string; citations: Array<{ citationData?: { metadata: { orgId: string } } }> }> }

        expect(citationSave.firstCall.args[0]).to.deep.equal({ session: mongoSession })
        const [docs, options] = insert.firstCall.args as [InsertedMessage[], { session: unknown }]
        expect(options.session).to.equal(mongoSession)
        expect(at(docs).sessionId).to.equal(conversation._id)
        expect(at(docs).orgId).to.equal(conversation.orgId)
        expect(at(docs).seq).to.equal(12)
        expect(at(docs).content).to.equal('Ship the connector in Q4.')
        expect(conversation.status).to.equal(CONVERSATION_STATUS.COMPLETE)
        expect(conversation.modelInfo).to.include({ modelKey: 'k2', modelFriendlyName: 'Model Two' })
        expect(conversation.save.firstCall.args[0]).to.deep.equal({ session: mongoSession })
        expect(response.title).to.equal('Roadmap')
        expect(at(at(response.messages).citations).citationData?.metadata.orgId).to.equal(orgId)
      })

      it(`throws "${failText}" when the conversation save returns nothing`, async () => {
        const conversation = makeConversation({ save: sinon.stub().resolves(null) })
        stubAppend(1)

        const error = await rejection(save(asDoc(conversation), answer(), 'org'))

        expect(error).to.be.instanceOf(InternalServerError)
        expect((error as Error).message).to.equal(failText)
      })

      it('leaves the conversation unsaved when the answer cannot be appended', async () => {
        const conversation = makeConversation()
        stubAppend(null)

        const error = await rejection(save(asDoc(conversation), answer(), 'org'))

        expect(error).to.be.instanceOf(NotFoundError)
        expect(conversation.save.called).to.equal(false)
      })
    })
  }

  describe('failure and partial saves that the database silently drops', () => {
    it('markConversationFailed still records the failure locally when save returns nothing', async () => {
      const conversation = makeConversation({ save: sinon.stub().resolves(null) })
      const { insert } = stubAppend(2)

      await markConversationFailed(asDoc(conversation), 'PipesHub could not answer right now.')

      expect(at(insert.firstCall.args[0] as InsertedMessage[]).messageType).to.equal('error')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(conversation.conversationErrors).to.have.length(1)
    })

    it('markAgentConversationFailed does not throw when save returns nothing', async () => {
      const conversation = makeConversation({ agentKey: 'agent-1', save: sinon.stub().resolves(null) })
      stubAppend(2)

      await markAgentConversationFailed(asDoc(conversation), 'failed', null, 'llm_error')

      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(at(conversation.conversationErrors).errorType).to.equal('llm_error')
    })

    it('markAgentConversationFailed passes a database error on to the caller', async () => {
      const conversation = makeConversation({ agentKey: 'agent-1', save: sinon.stub().rejects(new Error('disk full')) })
      stubAppend(2)

      const error = await rejection(markAgentConversationFailed(asDoc(conversation), 'failed'))

      expect((error as Error).message).to.equal('disk full')
    })

    it('savePartialConversation keeps what the user saw even when save returns nothing', async () => {
      const conversation = makeConversation({ save: sinon.stub().resolves(null) })
      const { insert } = stubAppend(5)

      await savePartialConversation(asDoc(conversation), 'Ship the conn')

      const doc = at(insert.firstCall.args[0] as InsertedMessage[])
      expect(doc.content).to.equal('Ship the conn')
      expect(doc.status).to.equal('stopped')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.STOPPED)
    })

    it('replaceMessageWithError still marks the conversation failed when the message is gone', async () => {
      const conversation = makeConversation()
      sinon.stub(ChatSessionMessage, 'findById').resolves(null)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace')
      const messageId = new mongoose.Types.ObjectId()

      await replaceMessageWithError(asDoc(conversation), messageId.toString(), 'Please send your message again.')

      expect(replace.called).to.equal(false)
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(conversation.save.calledOnce).to.equal(true)
    })
  })

  describe('reserveRun / releaseRun', () => {
    const ORG = new mongoose.Types.ObjectId()
    const USER = new mongoose.Types.ObjectId()
    const mine = (id: unknown): Record<string, unknown> => ({ _id: id, orgId: ORG, userId: USER, isDeleted: false })

    let store: InMemoryChatStore
    const addThread = (fields: Record<string, unknown> = {}): ReturnType<InMemoryChatStore['addSession']> =>
      store.addSession({ orgId: ORG, userId: USER, isDeleted: false, ...fields })
    const fakeClock = (): sinon.SinonFakeTimers => sinon.useFakeTimers({ now: Date.now() })

    beforeEach(() => {
      store = new InMemoryChatStore()
      store.install()
    })

    it('marks an idle thread in progress and remembers what it was', async () => {
      const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })

      const reservation = await reserveRun(mine(thread._id))

      expect(reservation?.previousStatus).to.equal(CONVERSATION_STATUS.COMPLETE)
      expect(String(reservation?.sessionId)).to.equal(String(thread._id))
      expect(thread.status).to.equal(CONVERSATION_STATUS.INPROGRESS)
    })

    it('returns null for a thread the caller does not own, and leaves it alone', async () => {
      const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })

      const reservation = await reserveRun({ ...mine(thread._id), userId: new mongoose.Types.ObjectId() })

      expect(reservation).to.equal(null)
      expect(thread.status).to.equal(CONVERSATION_STATUS.COMPLETE)
    })

    it('lets exactly one of two simultaneous messages through', async () => {
      const clock = fakeClock()
      const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })

      const first = reserveRun(mine(thread._id), 1000)
      const second = reserveRun(mine(thread._id), 1000)
      const outcomes = Promise.allSettled([first, second])
      await clock.tickAsync(1500)
      const results = await outcomes

      expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1)
      const refused = results.find((r) => r.status === 'rejected') as PromiseRejectedResult
      expect(refused.reason).to.be.instanceOf(ConflictError)
      expect(thread.status).to.equal(CONVERSATION_STATUS.INPROGRESS)
    })

    it('refuses with a conflict when a run is still going after the settle window, without changing the thread', async () => {
      const clock = fakeClock()
      const thread = addThread({ status: CONVERSATION_STATUS.INPROGRESS, lastActivityAt: Date.now() })

      const outcome = rejection(reserveRun(mine(thread._id), 1000))
      await clock.tickAsync(1500)

      expect(await outcome).to.be.instanceOf(ConflictError)
      expect(thread.status).to.equal(CONVERSATION_STATUS.INPROGRESS)
    })

    it('waits out a run that is being cancelled instead of refusing a quick Stop-then-send', async () => {
      const clock = fakeClock()
      const thread = addThread({ status: CONVERSATION_STATUS.INPROGRESS, lastActivityAt: Date.now() })

      const reserved = reserveRun(mine(thread._id), 5000)
      await clock.tickAsync(500)
      thread.set('status', CONVERSATION_STATUS.STOPPED)
      await clock.tickAsync(500)
      const reservation = await reserved

      expect(reservation?.previousStatus).to.equal(CONVERSATION_STATUS.STOPPED)
      expect(thread.status).to.equal(CONVERSATION_STATUS.INPROGRESS)
    })

    it('does not treat a stale in-progress status as a live run (a crashed server must not lock the thread)', async () => {
      const thread = addThread({ status: CONVERSATION_STATUS.INPROGRESS, lastActivityAt: Date.now() - 2 * 60 * 60 * 1000 })

      const reservation = await reserveRun(mine(thread._id))

      expect(reservation?.previousStatus, 'the abandoned run is recorded as failed, not as in progress').to.equal(
        CONVERSATION_STATUS.FAILED,
      )
    })

    it('does not block a thread that has no activity timestamp', async () => {
      const thread = addThread({ status: CONVERSATION_STATUS.INPROGRESS, lastActivityAt: null })

      expect(await reserveRun(mine(thread._id))).to.not.equal(null)
    })

    it('does not read the thread inside a transaction, which would never see a status change', async () => {
      const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
      const update = ChatSession.findOneAndUpdate as unknown as sinon.SinonStub

      await reserveRun(mine(thread._id))

      expect(update.firstCall.args[2]).to.not.have.property('session')
    })

    describe('keepRunAlive', () => {
      const beat = (): sinon.SinonFakeTimers =>
        sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'], now: Date.now() })

      it('keeps a long-running run looking alive, so another request cannot take its thread', async () => {
        const clock = beat()
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))
        const stream = new PassThrough()
        keepRunAlive(stream, thread._id, reservation?.runId)

        await clock.tickAsync(45 * 60 * 1000)
        const takeover = rejection(reserveRun(mine(thread._id), 1000))
        await clock.tickAsync(1500)

        expect(await takeover).to.be.instanceOf(ConflictError)
        expect(Date.now() - (thread.lastActivityAt as number)).to.be.lessThan(2 * 60 * 1000)
        stream.destroy()
      })

      it('stops when the stream closes, and no longer changes the thread', async () => {
        const clock = beat()
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))
        const stream = new PassThrough()
        keepRunAlive(stream, thread._id, reservation?.runId)
        stream.destroy()
        await clock.tickAsync(0)
        const stamped = thread.lastActivityAt

        await clock.tickAsync(10 * 60 * 1000)

        expect(thread.lastActivityAt).to.equal(stamped)
      })

      it('ignores a thread that has since been taken over by another run', async () => {
        const clock = beat()
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const older = await reserveRun(mine(thread._id))
        thread.set('status', CONVERSATION_STATUS.COMPLETE)
        await reserveRun(mine(thread._id))
        const stamped = thread.lastActivityAt
        const stream = new PassThrough()
        keepRunAlive(stream, thread._id, older?.runId)

        await clock.tickAsync(5 * 60 * 1000)

        expect(thread.lastActivityAt).to.equal(stamped)
        stream.destroy()
      })

      it('never records an update as a change to the conversation itself', async () => {
        const clock = beat()
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))
        const stream = new PassThrough()
        keepRunAlive(stream, thread._id, reservation?.runId)

        await clock.tickAsync(61 * 1000)

        const update = ChatSession.updateOne as unknown as sinon.SinonStub
        expect(update.firstCall.args[2]).to.deep.equal({ timestamps: false })
        stream.destroy()
      })
    })

    describe('releaseRun', () => {
      it('puts the thread back to what it was when the run never took it over', async () => {
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))

        await releaseRun(reservation)

        expect(thread.status).to.equal(CONVERSATION_STATUS.COMPLETE)
      })

      it('does not overwrite the outcome a run has already saved', async () => {
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))
        thread.set('status', CONVERSATION_STATUS.STOPPED)

        await releaseRun(reservation)

        expect(thread.status).to.equal(CONVERSATION_STATUS.STOPPED)
      })

      it('does not touch a newer request\'s run when an older reservation is released late', async () => {
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const older = await reserveRun(mine(thread._id))
        thread.set('status', CONVERSATION_STATUS.COMPLETE) // the older run saved its answer
        const newer = await reserveRun(mine(thread._id))

        await releaseRun(older)

        expect(thread.status, 'the newer run still owns the thread').to.equal(CONVERSATION_STATUS.INPROGRESS)
        await releaseRun(newer)
        expect(thread.status).to.equal(CONVERSATION_STATUS.COMPLETE)
      })

      it('does nothing without a reservation', async () => {
        const update = ChatSession.findOneAndUpdate as unknown as sinon.SinonStub

        await releaseRun(null)

        expect(update.called).to.equal(false)
      })

      it('never throws if the database is unreachable, so it cannot mask the original error', async () => {
        const thread = addThread({ status: CONVERSATION_STATUS.COMPLETE })
        const reservation = await reserveRun(mine(thread._id))
        sinon.restore()
        sinon.stub(ChatSession, 'findOneAndUpdate').throws(new Error('connection lost'))

        await releaseRun(reservation)
      })
    })
  })

  describe('formatPreviousConversations tool history', () => {
    it('replays past tool calls in the shape the AI service parses', () => {
      const history = formatPreviousConversations([
        { messageType: 'user_query', content: 'Open tickets?' } as IMessage,
        {
          messageType: 'bot_response',
          content: 'You have 3 open tickets.',
          parts: [
            { type: 'text', content: 'Looking…' },
            {
              type: 'tool_call',
              toolCallId: 'call-1',
              toolName: 'jira.search',
              args: '{"jql":"status = Open"}',
              status: 'completed',
              resultPreview: '[3 issues]',
              resultSummary: '3 open issues',
              artifactId: 'art-1',
            },
            { type: 'tool_call', toolCallId: 'call-2', toolName: 'jira.get', args: 'issue PA-1', status: 'failed', resultPreview: 'timeout' },
            { type: 'tool_call', toolCallId: 'call-3', args: '{}' },
            { type: 'tool_call', toolName: 'jira.count', args: '42', status: 'completed' },
          ],
        } as IMessage,
        { messageType: 'error', content: 'Something went wrong' } as IMessage,
      ])

      expect(history).to.have.length(2)
      expect(history[0]).to.not.have.property('tool_results')
      expect(at(history, 1).tool_results).to.deep.equal([
        {
          tool_id: 'call-1',
          tool_name: 'jira.search',
          args: { jql: 'status = Open' },
          result: '3 open issues',
          result_summary: '3 open issues',
          status: 'success',
          artifact_id: 'art-1',
        },
        { tool_id: 'call-2', tool_name: 'jira.get', result: 'timeout', status: 'error' },
        { tool_id: undefined, tool_name: 'jira.count', result: '', status: 'success' },
      ])
    })

    it('adds no tool history for an answer without tool calls', () => {
      const [turn] = formatPreviousConversations([
        { messageType: 'bot_response', content: 'Hi', parts: [{ type: 'text', content: 'Hi' }] } as IMessage,
      ])

      expect(turn).to.not.have.property('tool_results')
    })

    it('replays the questions a regenerated answer carries itself', () => {
      const payload = { name: 'ask_user_question', questions: [{ question: 'Which region?' }] }
      const history = formatPreviousConversations([
        {
          messageType: 'bot_response',
          content: '',
          tools: [{ toolName: 'ask_user_question', toolResult: payload }],
        } as unknown as IMessage,
      ])

      expect(at(history, 0).tool_results).to.deep.equal([
        {
          tool_id: 'ask_user_question',
          tool_name: 'internaltools__ask_user_question',
          result: JSON.stringify(payload),
          status: 'success',
        },
      ])
    })

    it('replays only the newest questions row when older regenerations left theirs behind', () => {
      const askRow = (question: string): IMessage => ({
        messageType: 'tool_call',
        content: '',
        tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question }] } }],
      } as unknown as IMessage)
      const history = formatPreviousConversations([
        { messageType: 'user_query', content: 'Which region?' } as IMessage,
        { messageType: 'bot_response', content: '' } as IMessage,
        askRow('discarded'),
        askRow('current'),
      ])

      const results = at(history, 1).tool_results as Array<{ result: string }>
      expect(results).to.have.length(1)
      expect(results[0]?.result).to.contain('current')
      expect(results[0]?.result).to.not.contain('discarded')
    })
  })

  describe('staleAskUserQuestionToolCallIds', () => {
    const row = (
      messageType: string,
      tools?: Array<{ toolName: string; toolResult: unknown }>,
    ): IMessage & { _id: mongoose.Types.ObjectId } => ({
      _id: new mongoose.Types.ObjectId(),
      messageType,
      content: '',
      ...(tools ? { tools } : {}),
    } as unknown as IMessage & { _id: mongoose.Types.ObjectId })
    const askTools = [{ toolName: 'ask_user_question', toolResult: { questions: [] } }]

    it('collects the questions rows on both sides of the turn being regenerated', () => {
      const before = row('tool_call', askTools)
      const bot = row('bot_response')
      const after = row('tool_call', askTools)
      const messages = [row('user_query'), before, bot, after, row('user_query')]

      const stale = staleAskUserQuestionToolCallIds(messages, bot._id)

      expect(stale.map((id) => id.toString())).to.have.members([
        before._id.toString(),
        after._id.toString(),
      ])
    })

    it('leaves another turn\'s questions row alone', () => {
      const otherTurnAsk = row('tool_call', askTools)
      const bot = row('bot_response')
      const messages = [otherTurnAsk, row('bot_response'), row('user_query'), bot]

      expect(staleAskUserQuestionToolCallIds(messages, bot._id)).to.deep.equal([])
    })

    it('ignores a tool_call row for some other tool', () => {
      const bot = row('bot_response')
      const messages = [
        bot,
        row('tool_call', [{ toolName: 'jira.search', toolResult: {} }]),
      ]

      expect(staleAskUserQuestionToolCallIds(messages, bot._id)).to.deep.equal([])
    })

    it('returns nothing when the turn is outside the window', () => {
      expect(
        staleAskUserQuestionToolCallIds([row('bot_response')], new mongoose.Types.ObjectId()),
      ).to.deep.equal([])
    })
  })

  describe('attachSharedBy', () => {
    const stubUsers = (users: Array<Record<string, unknown>>): sinon.SinonStub => {
      const chain = {
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(users),
      }
      return sinon.stub(Users, 'find').returns(chain as never)
    }

    it('names the sharer by first and last name, then email, then id, looking only in the caller org', async () => {
      const orgId = new mongoose.Types.ObjectId().toString()
      const byName = new mongoose.Types.ObjectId()
      const byEmail = new mongoose.Types.ObjectId()
      const unknown = new mongoose.Types.ObjectId()
      const find = stubUsers([
        { _id: byName, fullName: '  ', firstName: 'Grace', lastName: 'Hopper' },
        { _id: byEmail, firstName: ' ', email: ' ada@example.com ' },
      ])

      const result = await attachSharedBy(
        [
          { initiator: byName },
          { initiator: byEmail },
          { initiator: unknown },
          { initiator: byName, access: { isOwner: true } },
          {},
        ],
        orgId,
      )

      const [query] = find.firstCall.args as [{ orgId: mongoose.Types.ObjectId; isDeleted: boolean; _id: { $in: mongoose.Types.ObjectId[] } }]
      expect(query.orgId.toString()).to.equal(orgId)
      expect(query.isDeleted).to.equal(false)
      expect(query._id.$in.map(String).sort()).to.deep.equal([byName, byEmail, unknown].map(String).sort())
      expect(at(result).sharedBy).to.deep.equal({ userId: byName.toString(), name: 'Grace Hopper' })
      expect(at(result, 1).sharedBy).to.deep.equal({ userId: byEmail.toString(), name: 'ada@example.com' })
      expect(at(result, 2).sharedBy).to.deep.equal({ userId: unknown.toString(), name: unknown.toString() })
      expect(result[3]).to.not.have.property('sharedBy')
      expect(result[4]).to.not.have.property('sharedBy')
    })

    it('skips the lookup when no initiator is a valid id', async () => {
      const find = stubUsers([])

      const result = await attachSharedBy([{ initiator: 'not-an-id' }], new mongoose.Types.ObjectId().toString())

      expect(find.called).to.equal(false)
      expect(result[0]).to.not.have.property('sharedBy')
    })

    it('attachSharedByIfRecipient leaves the conversation alone without an org', async () => {
      const find = stubUsers([])
      const conversation = { initiator: new mongoose.Types.ObjectId() }

      expect(await attachSharedByIfRecipient(conversation, undefined)).to.equal(conversation)
      expect(find.called).to.equal(false)
    })

    it('attachSharedByIfRecipient names the sharer for a recipient', async () => {
      const initiator = new mongoose.Types.ObjectId()
      stubUsers([{ _id: initiator, fullName: 'Linus' }])

      const result = await attachSharedByIfRecipient({ initiator }, new mongoose.Types.ObjectId().toString())

      expect(result.sharedBy).to.deep.equal({ userId: initiator.toString(), name: 'Linus' })
    })
  })
})
