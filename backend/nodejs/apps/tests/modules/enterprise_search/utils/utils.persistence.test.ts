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
  markAgentConversationFailed,
  markConversationFailed,
  replaceMessageWithError,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  savePartialConversation,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { InternalServerError, NotFoundError } from '../../../../src/libs/errors/http.errors'
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
