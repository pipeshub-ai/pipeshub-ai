import { withOwnerGrant } from '../helpers/conversation-grant'
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import {
  appendMessageFeedback,
  appendMessages,
  findSessionIdsMatchingContent,
  getMessages,
  handleRegenerationSuccess,
  replaceMessageWithError,
  retireMessagesById,
  savePartialConversation,
  setMessageFields,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { MongoConversationHistory } from '../../../../src/modules/enterprise_search/services/conversation/persistence/message-history'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import type { IAIResponse, IChatSessionDocument, IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { chatArchivesScope, withListScope } from '../helpers/list-scope'
import { FakeSSEResponse, InMemoryChatStore, matchesFilter, oid, plain, settle } from '../controller/chat-test-harness'

/**
 * Every row write takes its own value of the session's `rev` (its `changeRev`), so a reader
 * holding a `rev` can ask for exactly the rows that changed after it. A removed row stays as an
 * emptied marker, and no read shows or counts it.
 */

const ORG = oid()
const OWNER = oid()
const AGENT_KEY = 'agent-1'
const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

const answer = (text: string): IAIResponse => ({ answer: text, citations: [], status: 'success' }) as unknown as IAIResponse
const row = (messageType: IMessage['messageType'], content: string, extra: Partial<IMessage> = {}): IMessage =>
  ({ messageType, content, ...extra }) as IMessage

function world(fields: Record<string, unknown> = {}) {
  const store = new InMemoryChatStore()
  store.install()
  const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, rev: 4, ...fields })
  // The handlers work on their own copy of the session, as they do after a read.
  const doc = ChatSession.hydrate(session.toObject()) as unknown as IChatSessionDocument
  const stamps = (): Array<number | undefined> => store.messagesOf(session._id).map((m) => m.changeRev as number | undefined)
  const rev = (): number => store.session(session._id)?.get('rev') as number
  return { store, session, doc, stamps, rev }
}

async function respond(handler: JsonHandler, params: Record<string, string>): Promise<{ body: any; error: unknown }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = withOwnerGrant({ headers: {}, params, query: {}, body: {}, user: { userId: OWNER, orgId: ORG }, context: {} })
  await handler(req as never, res as never, next as never)
  await settle()
  return { body: res.jsonBody, error: next.firstCall?.args[0] }
}

describe('row writer: change stamps', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('an append takes one rev per row, in row order, in the same session write as the seq block', async () => {
    const { store, session, stamps, rev } = world()

    await appendMessages(session._id, ORG, [row('user_query', 'q'), row('bot_response', 'a'), row('tool_call', 'card')])

    expect(stamps()).to.deep.equal([5, 6, 7])
    expect(rev()).to.equal(7)
    expect(store.writes.filter((w) => w.startsWith('chatSession.'))).to.deep.equal(['chatSession.allocateSeq'])
  })

  it('a regenerate stamps the replaced answer and each stale card above everything before, and adds no second bump', async () => {
    const { store, session, doc, stamps, rev } = world()
    const [, bot, card] = await appendMessages(session._id, ORG, [
      row('user_query', 'q'),
      row('bot_response', 'old answer'),
      row('tool_call', 'Which region?', { tools: [{ toolName: 'ask_user_question', toolResult: { q: 1 } }] }),
    ])
    const before = rev()

    await handleRegenerationSuccess(answer('new answer'), doc, bot!._id, String(ORG), null, undefined, undefined, [card!._id], {
      requestedBy: OWNER,
    })

    const rows = store.messagesOf(session._id)
    const answerRow = rows.find((m) => String(m._id) === String(bot!._id))!
    const cardRow = rows.find((m) => String(m._id) === String(card!._id))!
    expect(answerRow.content).to.equal('new answer')
    expect(answerRow.changeRev).to.be.greaterThan(before)
    expect(cardRow.changeRev).to.be.greaterThan(answerRow.changeRev as number)
    expect(rev(), 'the session rev ends at the newest stamp').to.equal(cardRow.changeRev)
    expect(new Set(stamps()).size).to.equal(stamps().length)
  })

  it('a removed card is emptied, stamped and marked by whom, and stays in the collection', async () => {
    const { store, session } = world()
    const [card] = await appendMessages(session._id, ORG, [
      row('tool_call', 'Which region?', { tools: [{ toolName: 'ask_user_question', toolResult: { q: 1 } }] }),
    ])

    expect(await retireMessagesById([card!._id], { sessionId: session._id, orgId: ORG }, null, OWNER)).to.equal(1)

    const stored = store.messagesOf(session._id)[0]!
    expect(stored).to.include({ content: '', changeRev: 6 })
    expect(stored.tools).to.deep.equal([])
    expect(stored.removedAt).to.be.instanceOf(Date)
    expect(String(stored.removedBy)).to.equal(String(OWNER))
    expect(await retireMessagesById([card!._id], { sessionId: session._id, orgId: ORG }), 'a removed row is not removed again').to.equal(0)
  })

  it('a removal is scoped to its own session', async () => {
    const { store, session } = world()
    const other = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
    const [foreign] = await appendMessages(other._id, ORG, [row('tool_call', 'theirs')])

    expect(await retireMessagesById([foreign!._id], { sessionId: session._id, orgId: ORG })).to.equal(0)
    expect(store.messagesOf(other._id)[0]).to.include({ content: 'theirs' })
    expect(store.messagesOf(other._id)[0]?.removedAt).to.equal(undefined)
  })

  it('a stamped field change keeps the other fields and honours its precondition', async () => {
    const { store, session, rev } = world()
    const [card] = await appendMessages(session._id, ORG, [row('tool_call', 'card')])

    expect(await setMessageFields(session._id, { _id: card!._id, content: 'not this' }, { content: 'x' })).to.equal(false)
    expect(await setMessageFields(session._id, { _id: card!._id, content: 'card' }, { status: 'stopped' })).to.equal(true)

    expect(store.messagesOf(session._id)[0]).to.include({ content: 'card', status: 'stopped', changeRev: rev() })
  })

  it('an error replace and a stopped replace each stamp the row they change', async () => {
    const { session, doc, store, rev } = world()
    const [bot] = await appendMessages(session._id, ORG, [row('bot_response', 'old')])

    await replaceMessageWithError(doc, bot!._id, 'It failed')
    expect(store.messagesOf(session._id)[0]).to.include({ messageType: 'error', changeRev: rev() })
    const afterError = rev()

    await savePartialConversation(doc, 'half an answer', null, { replaceMessageId: bot!._id })
    expect(store.messagesOf(session._id)[0]).to.include({ content: 'half an answer', changeRev: rev() })
    expect(rev()).to.equal(afterError + 1)
  })

  it('feedback moves neither the session rev nor the row stamp', async () => {
    const { session, stamps, rev } = world()
    const [bot] = await appendMessages(session._id, ORG, [row('bot_response', 'a')])
    const before = { rev: rev(), stamps: stamps() }

    await appendMessageFeedback(bot!._id, ORG, { isHelpful: true, feedbackProvider: OWNER })

    expect({ rev: rev(), stamps: stamps() }).to.deep.equal(before)
  })
})

describe('row writer: removed rows are hidden on every read', () => {
  afterEach(() => {
    sinon.restore()
  })

  /** One live answer and one removed card that still has text, as an older or half-written removal might. */
  function seeded(fields: Record<string, unknown> = {}) {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Release notes', ...fields })
    store.addMessage(session, { messageType: 'user_query', content: 'What changed?' })
    store.addMessage(session, { messageType: 'tool_call', content: 'needle card', removedAt: new Date() })
    store.addMessage(session, { messageType: 'bot_response', content: 'An answer.' })
    return { store, session }
  }
  const contents = (rows: Array<{ content?: unknown }>): unknown[] => rows.map((m) => m.content)

  it('getMessages, in both orders', async () => {
    const { session } = seeded()
    expect(contents(await getMessages(session._id, { orgId: ORG }))).to.deep.equal(['What changed?', 'An answer.'])
    expect(contents(await getMessages(session._id, { orgId: ORG, sort: -1, limit: 5 }))).to.deep.equal(['An answer.', 'What changed?'])
  })

  it('the history a turn is answered from', async () => {
    const { session } = seeded()
    const history = await new MongoConversationHistory().historyBefore(session._id, 99)
    expect(contents(history)).to.deep.equal(['What changed?', 'An answer.'])
  })

  it('the content search', async () => {
    const { store } = seeded()
    sinon.stub(ChatSessionMessage, 'aggregate').callsFake(((pipeline: Array<{ $match?: Record<string, unknown> }>) =>
      Promise.resolve(
        store.messages
          .filter((m) => matchesFilter(plain(m), pipeline[0]?.$match ?? {}))
          .map((m) => ({ _id: m.sessionId })),
      )) as never)

    expect(await findSessionIdsMatchingContent(String(ORG), 'needle')).to.deep.equal([])
    expect(await findSessionIdsMatchingContent(String(ORG), 'answer')).to.have.length(1)
  })

  it('the conversation detail: its rows and its count', async () => {
    const { session } = seeded()
    const { body, error } = await respond(controller.getConversationById(appConfig) as JsonHandler, { conversationId: String(session._id) })
    expect(error).to.equal(undefined)
    expect(contents(body.conversation.messages).sort()).to.deep.equal(['An answer.', 'What changed?'])
    expect(body.conversation.pagination.totalCount).to.equal(2)
    expect(body.meta.messageCount).to.equal(2)
  })

  it('the agent conversation detail: its rows and its count', async () => {
    const { session } = seeded({ sessionType: 'agent', agentKey: AGENT_KEY })
    const { body, error } = await respond(controller.getAgentConversationById(appConfig) as JsonHandler, {
      conversationId: String(session._id),
      agentKey: AGENT_KEY,
    })
    expect(error).to.equal(undefined)
    expect(contents(body.conversation.messages).sort()).to.deep.equal(['An answer.', 'What changed?'])
    expect(body.conversation.pagination.totalCount).to.equal(2)
  })

  it('the archived list', async () => {
    seeded({ isArchived: true, archivedBy: OWNER })
    const { body, error } = await respond(withListScope(controller.listAllArchivesConversation as JsonHandler, 'chat', chatArchivesScope), {})
    expect(error).to.equal(undefined)
    expect(contents(body.conversations[0].messages)).to.deep.equal(['What changed?', 'An answer.'])
  })

  it('feedback on a removed row is refused as not found', async () => {
    const { store, session } = seeded()
    const removed = store.messages.find((m) => m.removedAt !== undefined)!
    const res = new FakeSSEResponse()
    const next = sinon.stub()
    const req = withOwnerGrant({
      headers: {},
      params: { conversationId: String(session._id), messageId: String(removed._id) },
      query: {},
      body: { isHelpful: true },
      user: { userId: OWNER, orgId: ORG },
      context: {},
    })
    await controller.updateFeedback(req as never, res as never, next as never)
    expect((next.firstCall?.args[0] as { statusCode?: number } | undefined)?.statusCode).to.equal(404)
  })

  it('responses never carry the stamp, like the other internal row fields', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
    await appendMessages(session._id, ORG, [row('user_query', 'q')])
    const { body } = await respond(controller.getConversationById(appConfig) as JsonHandler, { conversationId: String(session._id) })
    expect(body.conversation.messages[0]).to.not.have.any.keys('changeRev', 'seq', 'sessionId', 'orgId')
  })
})
