import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import {
  allocateSeq,
  buildAIResponseMessage,
  appendMessages,
  buildUserQueryMessage,
  attachSharedBy,
  attachSharedByIfRecipient,
  formatPreviousConversations,
  markAgentConversationFailed,
  markConversationFailed,
  replaceMessageWithError,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  savePartialConversation,
  staleAskUserQuestionToolCallIds,
  sanitizeMessageForPersistence,
  updateMessageById,
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

/** The terminal session write: a conditional `updateOne`, never a full-document save of the in-memory copy. */
const stubSessionUpdate = (result: number | Error = 1): sinon.SinonStub => {
  const current = ChatSession.updateOne as unknown as Partial<sinon.SinonStub>
  current.restore?.()
  const stub = sinon.stub(ChatSession, 'updateOne')
  return result instanceof Error ? stub.rejects(result) : stub.resolves({ matchedCount: result } as never)
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
  sinon.stub(Citation, 'insertMany').callsFake(((docs: unknown) => Promise.resolve(docs)) as never)

const rejection = async (promise: Promise<unknown>): Promise<unknown> => {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the promise to reject')
}

describe('user_query authorship', () => {
  it('carries only the author fields it was given, and nothing for a legacy caller', () => {
    const author = new mongoose.Types.ObjectId()
    const plain = buildUserQueryMessage('q')
    const stamped = buildUserQueryMessage('q', undefined, 'quick', undefined, { authorUserId: author, clientMessageId: 'k', filesShared: false, runId: undefined })
    expect(plain).to.not.have.any.keys('authorUserId', 'clientMessageId', 'filesShared', 'shareToolResults', 'runId')
    expect(stamped).to.include({ authorUserId: author, clientMessageId: 'k', filesShared: false })
    expect(stamped).to.not.have.any.keys('runId', 'shareToolResults')
  })
})

describe('Saving chat answers (enterprise search utils)', () => {
  beforeEach(() => {
    stubSessionUpdate()
  })
  afterEach(() => {
    sinon.restore()
  })

  describe('no signed URL is persisted on any assistant write path (PH01-13)', () => {
    const SIGNED = 'https://b.s3.amazonaws.com/x?X-Amz-Signature=abc123'
    const text = `See [f](${SIGNED}) and bare ${SIGNED} but keep [docs](https://example.com/p?a=1).`
    const assertClean = (content: unknown): void => {
      expect(content).to.be.a('string')
      expect(content).to.not.contain('X-Amz-Signature')
      expect(content).to.contain('[docs](https://example.com/p?a=1)')
      expect(content).to.contain('[link removed]')
    }

    it('saveCompleteConversation', async () => {
      const { insert } = stubAppend(1)
      await saveCompleteConversation(asDoc(makeConversation()), answer({ answer: text }), 'org')
      assertClean(at(insert.firstCall.args[0] as InsertedMessage[]).content)
    })

    it('saveCompleteAgentConversation', async () => {
      const { insert } = stubAppend(1)
      await saveCompleteAgentConversation(asDoc(makeConversation({ agentKey: 'a' })), answer({ answer: text }), 'org')
      assertClean(at(insert.firstCall.args[0] as InsertedMessage[]).content)
    })

    it('savePartialConversation (append)', async () => {
      const { insert } = stubAppend(1)
      await savePartialConversation(asDoc(makeConversation()), text)
      assertClean(at(insert.firstCall.args[0] as InsertedMessage[]).content)
    })

    it('savePartialConversation (regenerate replace)', async () => {
      const id = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves({ sessionId: 's', orgId: 'o', seq: 3 } as never)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({} as never)
      await savePartialConversation(asDoc(makeConversation()), text, null, { replaceMessageId: id })
      assertClean((replace.firstCall.args[1] as IMessage).content)
    })

    it('regenerate that replaces a message with a complete answer', async () => {
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({} as never)
      sinon.stub(ChatSessionMessage, 'findById').resolves({ sessionId: 's', orgId: 'o', seq: 3 } as never)
      await updateMessageById(new mongoose.Types.ObjectId(), buildAIResponseMessage({ statusCode: 200, data: { answer: text } } as any))
      assertClean((replace.firstCall.args[1] as IMessage).content)
    })

    it('error rows carrying content', async () => {
      const { insert } = stubAppend(1)
      await markConversationFailed(asDoc(makeConversation()), text)
      const doc = at(insert.firstCall.args[0] as InsertedMessage[])
      expect(doc.messageType).to.equal('error')
      assertClean(doc.content)
    })

    it('leaves user queries untouched', () => {
      const msg = { messageType: 'user_query', content: text } as IMessage
      expect(sanitizeMessageForPersistence(msg)).to.equal(msg)
    })

    describe('every persisted field (sanitizeMessageForPersistence)', () => {
      const clean = (v: unknown): void => {
        const json = JSON.stringify(v)
        expect(json).to.not.contain('X-Amz-Signature')
        expect(json).to.contain('[link removed]')
      }
      const bot = (extra: Record<string, unknown>): IMessage =>
        ({ messageType: 'bot_response', content: 'ok', ...extra }) as unknown as IMessage
      const out = (extra: Record<string, unknown>): any => sanitizeMessageForPersistence(bot(extra))

      it('reasoning turns', () => clean(out({ reasoning: [{ turnIndex: 0, content: `see ${SIGNED}` }] }).reasoning))
      it('tool results, nested', () =>
        clean(out({ tools: [{ toolName: 't', toolResult: { a: [{ url: SIGNED }, `x ${SIGNED}`] } }] }).tools))
      it('parts incl. nested sub_agent', () =>
        clean(out({ parts: [{ type: 'sub_agent', parts: [{ type: 'text', text: SIGNED }] }] }).parts))
      it('referenceData strings', () =>
        clean(out({ referenceData: [{ name: 'f', webUrl: SIGNED, metadata: { k: SIGNED } }] }).referenceData))
      it('citation excerpt and context', () =>
        clean(out({ citations: [{ excerpt: `[a](${SIGNED})`, context: SIGNED }] }).citations))
      it('follow-up questions', () => clean(out({ followUpQuestions: [{ question: SIGNED }] }).followUpQuestions))

      it('leaves unsigned values, record markers, non-strings and structure intact', () => {
        const id = new mongoose.Types.ObjectId()
        const when = new Date()
        const msg = bot({
          content: '::artifact[a.csv](record:r1){text/csv|d|r1||1} https://example.com/a?b=1',
          citations: [{ citationId: id, relevanceScore: 0.5, excerpt: 'https://example.com' }],
          tools: [{ toolName: 't', toolResult: { n: 1, ok: true, none: null, list: [1, 'x'], when } }],
          referenceData: [{ webUrl: 'http://h/record/r1/preview' }],
        })
        const result = sanitizeMessageForPersistence(msg) as any
        expect(result).to.deep.equal(msg)
        expect(result.citations[0].citationId).to.equal(id)
        expect(result.tools[0].toolResult.when).to.equal(when)
      })

      it('does not mutate its input', () => {
        const msg = bot({ tools: [{ toolName: 't', toolResult: SIGNED }] })
        sanitizeMessageForPersistence(msg)
        expect((msg as any).tools[0].toolResult).to.equal(SIGNED)
      })

      it('bounds deep and large objects without throwing', () => {
        let deep: any = { s: SIGNED }
        for (let i = 0; i < 5000; i++) deep = { d: deep }
        const wide = Array.from({ length: 200_000 }, () => SIGNED)
        const started = Date.now()
        const result = sanitizeMessageForPersistence(bot({ tools: [{ toolName: 't', toolResult: deep }], parts: wide })) as any
        expect(Date.now() - started).to.be.lessThan(5000)
        expect(JSON.stringify(result)).to.not.contain('X-Amz-Signature')
      })
    })

    it('keeps record markers, citations and normal links', async () => {
      const { insert } = stubAppend(1)
      const kept =
        'Fact [1](http://h/record/r1/preview#blockIndex=0) https://example.com/a\n\n::artifact[a.csv](record:r1){text/csv|d|r1||1}'
      await savePartialConversation(asDoc(makeConversation()), kept)
      expect(at(insert.firstCall.args[0] as InsertedMessage[]).content).to.equal(kept)
    })
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

    it('PH05-03: bumps rev with the sequence block, in the same atomic update', async () => {
      const { allocate } = stubAppend(2)

      await allocateSeq(new mongoose.Types.ObjectId(), 2)

      expect((allocate.firstCall.args[1] as { $inc: unknown }).$inc).to.deep.equal({ nextSeq: 2, rev: 1 })
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
        const update = stubSessionUpdate()
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

        expect(citationSave.firstCall.args[1]).to.deep.equal({ session: mongoSession })
        const [docs, options] = insert.firstCall.args as [InsertedMessage[], { session: unknown }]
        expect(options.session).to.equal(mongoSession)
        expect(at(docs).sessionId).to.equal(conversation._id)
        expect(at(docs).orgId).to.equal(conversation.orgId)
        expect(at(docs).seq).to.equal(12)
        expect(at(docs).content).to.equal('Ship the connector in Q4.')
        expect(conversation.status).to.equal(CONVERSATION_STATUS.COMPLETE)
        expect(conversation.modelInfo).to.include({ modelKey: 'k2', modelFriendlyName: 'Model Two' })
        const [filter, change, updateOptions] = update.firstCall.args as [Record<string, unknown>, { $set: Record<string, unknown> }, unknown]
        expect(filter).to.deep.equal({ _id: conversation._id, isDeleted: false })
        expect(change.$set).to.include({ status: CONVERSATION_STATUS.COMPLETE, 'modelInfo.modelKey': 'k2', 'modelInfo.chatMode': 'deep' })
        expect(updateOptions).to.deep.equal({ session: mongoSession })
        expect(response.title).to.equal('Roadmap')
        expect(at(at(response.messages).citations).citationData?.metadata.orgId).to.equal(orgId)
      })

      it(`throws "${failText}" when no live session matches the write`, async () => {
        const conversation = makeConversation()
        stubAppend(1)
        stubSessionUpdate(0)

        const error = await rejection(save(asDoc(conversation), answer(), 'org'))

        expect(error).to.be.instanceOf(InternalServerError)
        expect((error as Error).message).to.equal(failText)
      })

      it('leaves the conversation unsaved when the answer cannot be appended', async () => {
        const conversation = makeConversation()
        stubAppend(null)
        const update = stubSessionUpdate()

        const error = await rejection(save(asDoc(conversation), answer(), 'org'))

        expect(error).to.be.instanceOf(NotFoundError)
        expect(update.called).to.equal(false)
      })
    })
  }

  describe('failure and partial saves that the database silently drops', () => {
    it('markConversationFailed still records the failure locally when no live session matches', async () => {
      const conversation = makeConversation()
      const { insert } = stubAppend(2)
      stubSessionUpdate(0)

      await markConversationFailed(asDoc(conversation), 'PipesHub could not answer right now.')

      expect(at(insert.firstCall.args[0] as InsertedMessage[]).messageType).to.equal('error')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(conversation.conversationErrors).to.have.length(1)
    })

    it('markAgentConversationFailed does not throw when no live session matches', async () => {
      const conversation = makeConversation({ agentKey: 'agent-1' })
      stubAppend(2)
      stubSessionUpdate(0)

      await markAgentConversationFailed(asDoc(conversation), 'failed', null, 'llm_error')

      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(at(conversation.conversationErrors).errorType).to.equal('llm_error')
    })

    it('markAgentConversationFailed passes a database error on to the caller', async () => {
      const conversation = makeConversation({ agentKey: 'agent-1' })
      stubAppend(2)
      stubSessionUpdate(new Error('disk full'))

      const error = await rejection(markAgentConversationFailed(asDoc(conversation), 'failed'))

      expect((error as Error).message).to.equal('disk full')
    })

    it('savePartialConversation keeps what the user saw even when no live session matches', async () => {
      const conversation = makeConversation()
      const { insert } = stubAppend(5)
      stubSessionUpdate(0)

      await savePartialConversation(asDoc(conversation), 'Ship the conn')

      const doc = at(insert.firstCall.args[0] as InsertedMessage[])
      expect(doc.content).to.equal('Ship the conn')
      expect(doc.status).to.equal('stopped')
      expect(conversation.status).to.equal(CONVERSATION_STATUS.STOPPED)
    })

    it('replaceMessageWithError still marks the conversation failed when the message is gone', async () => {
      const conversation = makeConversation()
      const sessionWrite = stubSessionUpdate()
      sinon.stub(ChatSessionMessage, 'findById').resolves(null)
      const replace = sinon.stub(ChatSessionMessage, 'findOneAndReplace')
      const messageId = new mongoose.Types.ObjectId()

      await replaceMessageWithError(asDoc(conversation), messageId.toString(), 'Please send your message again.')

      expect(replace.called).to.equal(false)
      expect(conversation.status).to.equal(CONVERSATION_STATUS.FAILED)
      expect(sessionWrite.calledOnce).to.equal(true)
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

    it('queries Users with the exact org-scoped filter and projection', async () => {
      const orgId = new mongoose.Types.ObjectId().toString()
      const initiator = new mongoose.Types.ObjectId()
      const chain = {
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([{ _id: initiator, email: 'a@b.c' }]),
      }
      const find = sinon.stub(Users, 'find').returns(chain as never)

      await attachSharedBy([{ initiator }], orgId)

      expect(find.calledOnce).to.equal(true)
      expect(find.calledWithMatch({ isDeleted: false })).to.equal(true)
      const [filter] = find.firstCall.args as [Record<string, any>]
      expect(Object.keys(filter).sort()).to.deep.equal(['_id', 'isDeleted', 'orgId'])
      expect(filter.orgId).to.be.instanceOf(mongoose.Types.ObjectId)
      expect(filter._id.$in[0]).to.be.instanceOf(mongoose.Types.ObjectId)
      expect(chain.select.calledOnceWithExactly('fullName firstName lastName email')).to.equal(true)
      expect(chain.lean.calledOnce).to.equal(true)
    })

    it('prefers fullName, trims it, and falls back to the id when the user row is missing', async () => {
      const full = new mongoose.Types.ObjectId()
      const missing = new mongoose.Types.ObjectId()
      stubUsers([{ _id: full, fullName: '  Ada Lovelace  ', firstName: 'X', lastName: 'Y', email: 'e@x.y' }])

      const result = await attachSharedBy([{ initiator: full }, { initiator: missing }], new mongoose.Types.ObjectId().toString())

      expect(at(result).sharedBy).to.deep.equal({ userId: full.toString(), name: 'Ada Lovelace' })
      expect(at(result, 1).sharedBy).to.deep.equal({ userId: missing.toString(), name: missing.toString() })
    })

    it('does not query when every conversation is owned by the caller', async () => {
      const find = stubUsers([])
      const owned = [
        { initiator: new mongoose.Types.ObjectId(), isOwner: true },
        { initiator: new mongoose.Types.ObjectId(), access: { isOwner: true } },
      ]

      const result = await attachSharedBy(owned, new mongoose.Types.ObjectId().toString())

      expect(find.called).to.equal(false)
      expect(result).to.deep.equal(owned)
      expect(result[0]).to.not.have.property('sharedBy')
    })

    it('leaves an invalid initiator out of the query but still labels it by id', async () => {
      const valid = new mongoose.Types.ObjectId()
      const find = stubUsers([{ _id: valid, fullName: 'Valid' }])

      const result = await attachSharedBy([{ initiator: 'nope' }, { initiator: valid }], new mongoose.Types.ObjectId().toString())

      const [filter] = find.firstCall.args as [{ _id: { $in: unknown[] } }]
      expect(filter._id.$in).to.have.length(1)
      expect(at(result).sharedBy).to.deep.equal({ userId: 'nope', name: 'nope' })
      expect(at(result, 1).sharedBy?.name).to.equal('Valid')
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
