import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { appendMessages } from '../../../../src/modules/enterprise_search/utils/utils'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { conversationShareParamsSchema } from '../../../../src/modules/enterprise_search/validators/es_validators'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * Write-shared conversations, end to end against the in-memory store and a fake AI
 * service: B may add messages to A's chat, every turn runs as its requester, and
 * each turn is shown in full only to its author.
 */

const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never
const ORG = oid()
const A = oid() // owner
const B = oid() // shared for writing
const C = oid() // shared for reading
const AGENT_KEY = 'agent-1'

const NAMES = new Map([[String(A), 'A'], [String(B), 'B'], [String(C), 'C']])
/** Records each user can read. rA only A, rB only B, rShared everyone. */
const READABLE: Record<string, string[]> = {
  [String(A)]: ['rA', 'rShared'],
  [String(B)]: ['rB', 'rShared'],
  [String(C)]: ['rShared'],
}

// Stored answers carry the canonical link form of each citation.
const A1 = '[1](https://app/rA)'
const S2 = '[2](https://app/rShared)'
const B1 = '[1](https://app/rB)'
const A_CHUNK = 'A-only chunk text about the merger'
const B_CHUNK = 'B-only chunk text about the layoffs'
const A_TOOL = 'A-TOOL-RESULT'
const B_TOOL = 'B-TOOL-RESULT'

type Handler = (req: never, res: never, next: never) => Promise<unknown>
type StreamHandler = (req: never, res: never) => Promise<unknown>

const requestFor = (who: Types.ObjectId, params: Record<string, string>, body: Record<string, unknown> = {}): never =>
  ({
    headers: { authorization: `Bearer token-${NAMES.get(String(who))}` },
    params,
    body,
    query: {},
    user: { userId: who, orgId: ORG, email: `${NAMES.get(String(who))}@example.com` },
    context: { requestId: 'req-write-shared' },
  }) as never

const citationOf = (store: InMemoryChatStore, chunkIndex: number, recordId: string, content: string) => {
  const doc = new Citation({
    content,
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: { orgId: String(ORG), mimeType: 'text/plain', recordId, recordName: `name-${recordId}`, origin: 'UPLOAD', connector: 'KB', webUrl: `https://app/${recordId}` },
  })
  store.citations.push(doc)
  return { citationId: doc._id }
}

interface World {
  store: InMemoryChatStore
  ai: FakeAIBackend
  chatId: string
  agentChatId: string
  messages: () => Array<Record<string, any>>
}

function world(): World {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  // Python's access check answers for whoever signed the token, as it does for real.
  ai.replyWith(/\/chat\/records\/access$/, (call) => {
    const token = (call.headers.authorization ?? '').replace('Bearer ', '')
    const viewer = String((jwt.decode(token) as { userId: string }).userId)
    const ids = (call.body.recordIds as string[]) ?? []
    return { status: 200, body: { accessible: ids.filter((id) => READABLE[viewer]?.includes(id)) } }
  })

  const sharedWith = [
    { userId: B, accessLevel: 'write' },
    { userId: C, accessLevel: 'read' },
  ]
  const chat = store.addSession({ orgId: ORG, userId: A, initiator: A, title: 'Deal room', sessionType: 'chat', isShared: true, sharedWith })
  store.addMessage(chat, { messageType: 'user_query', content: 'What about the merger?' })
  store.addMessage(chat, {
    messageType: 'bot_response',
    content: `The merger closes in May ${A1}. Everyone knows the office moved ${S2}.`,
    citations: [citationOf(store, 1, 'rA', A_CHUNK), citationOf(store, 2, 'rShared', 'shared chunk')],
    tools: [{ toolName: 'search', toolResult: A_TOOL }],
    reasoning: [{ content: A_TOOL }],
  })
  const agentChat = store.addSession({
    orgId: ORG, userId: A, initiator: A, title: 'Agent room', sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat', isShared: true, sharedWith,
  })
  store.addMessage(agentChat, { messageType: 'user_query', content: 'Agent question' })
  store.addMessage(agentChat, { messageType: 'bot_response', content: `Agent answer ${A1}.`, citations: [citationOf(store, 1, 'rA', A_CHUNK)] })
  store.writes.length = 0
  return { store, ai, chatId: String(chat._id), agentChatId: String(agentChat._id), messages: () => store.messagesOf(chat._id) as Array<Record<string, any>> }
}

/** One streamed turn: the handler runs, the fake AI service answers, the run is saved. */
async function streamTurn(
  w: World,
  handler: StreamHandler,
  who: Types.ObjectId,
  params: Record<string, string>,
  body: Record<string, unknown>,
  answer: { answer: string; citations?: unknown[] },
): Promise<FakeSSEResponse> {
  const res = new FakeSSEResponse()
  await handler(requestFor(who, params, body), res as never)
  await settle()
  w.ai.send('RUN_FINISHED', { runId: 'run-1', result: { confidence: 'High', citations: [], ...answer } })
  w.ai.finish()
  await res.ended
  await settle()
  return res
}

const bCitation = {
  content: B_CHUNK,
  chunkIndex: 1,
  citationType: 'vectordb|document',
  metadata: { orgId: String(ORG), mimeType: 'text/plain', recordId: 'rB', recordName: 'name-rB', origin: 'UPLOAD', connector: 'KB', webUrl: 'https://app/rB' },
}

const addAsB = (w: World, query = 'B asks about the layoffs') =>
  streamTurn(w, controller.addMessageStream(appConfig) as StreamHandler, B, { conversationId: w.chatId }, { query }, {
    answer: `Layoffs are planned ${B1}.`,
    citations: [bCitation],
  })

async function getAs(w: World, who: Types.ObjectId): Promise<Array<Record<string, any>>> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await (controller.getConversationById(appConfig) as Handler)(requestFor(who, { conversationId: w.chatId }), res as never, next as never)
  await settle()
  expect(next.firstCall?.args[0], 'GET error').to.equal(undefined)
  return (res.jsonBody as any).conversation.messages
}

describe('write-shared conversations: users add messages, each turn shown in full only to its author', () => {
  afterEach(() => sinon.restore())

  describe('a write-shared user adds a message and the turn runs as them', () => {
    it('asks the AI service with the requester’s own credentials and answers from their access', async () => {
      const w = world()
      const res = await addAsB(w)

      expect(res.eventsOf('RUN_ERROR')).to.deep.equal([])
      expect(w.ai.streamCalls).to.have.length(1)
      expect(w.ai.streamCalls[0]?.headers.authorization, 'the requester’s token, never the owner’s').to.equal('Bearer token-B')
      const frame = res.eventsOf('RUN_FINISHED')[0]?.data.result as { conversation: { messages: Array<Record<string, any>> } }
      const answer = frame.conversation.messages.at(-1)!
      expect(answer.content).to.equal(`Layoffs are planned ${B1}.`)
      expect(answer.citations[0].citationData.content, 'B reads the source only B can read').to.equal(B_CHUNK)
      expect(answer.redaction).to.equal(undefined)
    })

    it('stores who each message was generated for, and leaves the owner as the conversation owner', async () => {
      const w = world()
      await addAsB(w)

      const rows = w.messages()
      expect(rows.map((m) => m.messageType)).to.deep.equal(['user_query', 'bot_response', 'user_query', 'bot_response'])
      expect(rows[0]!.authorUserId, 'earlier rows stay unstamped: their author is the initiator').to.equal(undefined)
      expect(String(rows[2]!.authorUserId)).to.equal(String(B))
      expect(String(rows[3]!.authorUserId)).to.equal(String(B))
      expect(rows.map((m) => m.seq)).to.deep.equal([1, 2, 3, 4])
      const session = w.store.sessions[0]!
      expect(String(session.userId)).to.equal(String(A))
      expect(String(session.initiator)).to.equal(String(A))
    })

    it('the non-streaming route runs the same way and redacts the reply it returns for the requester', async () => {
      const w = world()
      w.ai.reply(/\/api\/v1\/chat$/, 200, { answer: `Layoffs are planned ${B1}.`, citations: [bCitation], confidence: 'High' })
      const res = new FakeSSEResponse()
      const next = sinon.stub()
      await (controller.addMessage(appConfig) as Handler)(requestFor(B, { conversationId: w.chatId }, { query: 'B asks' }), res as never, next as never)
      await settle()

      expect(next.firstCall?.args[0]).to.equal(undefined)
      const messages = (res.jsonBody as any).conversation.messages as Array<Record<string, any>>
      const json = JSON.stringify(messages)
      expect(json, 'A’s source is redacted in B’s reply').to.not.include(A_CHUNK)
      expect(json).to.not.include(A_TOOL)
      expect(json, 'B’s own turn is whole').to.include(B_CHUNK)
      expect(w.ai.calls.find((c) => /\/api\/v1\/chat$/.test(c.url))?.headers.authorization).to.equal('Bearer token-B')
      expect(String(w.messages().at(-1)!.authorUserId)).to.equal(String(B))
    })
  })

  describe('the owner sees the writer’s turn redacted for the sources the owner cannot read', () => {
    it('redacts B’s answer for A, with a way to request access, and keeps A’s own turn whole', async () => {
      const w = world()
      await addAsB(w)

      const messages = await getAs(w, A)
      const json = JSON.stringify(messages)
      expect(json, 'raw JSON carries none of B’s source text').to.not.include(B_CHUNK)
      expect(json).to.not.include('name-rB')
      const bAnswer = messages.at(-1)!
      expect(bAnswer.redaction).to.include({ redacted: true })
      expect(bAnswer.content).to.equal('')
      expect(bAnswer.citations[0]).to.include({ redacted: true })
      expect(bAnswer.citations[0].requestAccess).to.include({ recordId: 'rB', kind: 'native' })
      expect(messages[1]!.content, 'A’s own answer is whole').to.equal(`The merger closes in May ${A1}. Everyone knows the office moved ${S2}.`)
      expect(messages[1]!.citations[0].citationData.content).to.equal(A_CHUNK)
    })

    it('B sees A’s turn redacted the same way', async () => {
      const w = world()
      const messages = await getAs(w, B)
      const json = JSON.stringify(messages)
      expect(json).to.not.include(A_CHUNK)
      expect(messages[1]!.content).to.equal(`{{redacted}} Everyone knows the office moved ${S2}.`)
      expect(messages[1]!.redaction).to.include({ partial: true })
    })
  })

  describe('a read-only user cannot add messages', () => {
    /** Calls a public route the way Express does (with `next`) and reports what escaped. */
    const callRoute = async (handler: Handler, req: never) => {
      const escaped: unknown[] = []
      const onRejection = (reason: unknown) => escaped.push(reason)
      process.on('unhandledRejection', onRejection)
      const res = new FakeSSEResponse()
      const next = sinon.stub()
      try {
        await Promise.resolve(handler(req, res as never, next as never)).catch((e) => escaped.push(e))
        await settle()
      } finally {
        process.off('unhandledRejection', onRejection)
      }
      return { next, res, escaped }
    }

    it('answers 403 on the public streaming route through next, before any stream, with no rejection escaping', async () => {
      const w = world()
      for (const [handler, params] of [
        [controller.addMessageStream(appConfig) as Handler, { conversationId: w.chatId }],
      ] as const) {
        const { next, res, escaped } = await callRoute(handler, requestFor(C, { ...params }, { query: 'let me in' }))
        expect(next.firstCall?.args[0]?.statusCode).to.equal(403)
        expect(escaped, 'no unhandled rejection').to.deep.equal([])
        expect(res.body, 'no stream was started').to.equal('')
      }
      expect(w.ai.streamCalls).to.deep.equal([])
      expect(w.store.writes).to.deep.equal([])
      expect(w.messages()).to.have.length(2)
    })

    it('the internal twin still rejects for its own wrapper to forward', async () => {
      const w = world()
      const err = await (controller.addMessageStream(appConfig) as StreamHandler)(
        requestFor(C, { conversationId: w.chatId }, { query: 'let me in' }),
        new FakeSSEResponse() as never,
      ).catch((e) => e)
      expect(err.statusCode).to.equal(403)
    })

    it('answers 403 on the non-streaming routes', async () => {
      const w = world()
      for (const [handler, params] of [
        [controller.addMessage(appConfig) as Handler, { conversationId: w.chatId }],
      ] as const) {
        const { next, escaped } = await callRoute(handler, requestFor(C, { ...params }, { query: 'let me in' }))
        expect(next.firstCall?.args[0]?.statusCode).to.equal(403)
        expect(escaped).to.deep.equal([])
      }
      expect(w.ai.calls.filter((c) => !/records\/access/.test(c.url))).to.deep.equal([])
      expect(w.store.writes).to.deep.equal([])
    })

    it('does not let a reader through because someone else in the list can write', async () => {
      const w = world()
      // C is a reader; B is a writer. Matching 'a writer exists' and 'C is listed' separately would admit C.
      const { next } = await callRoute(controller.addMessageStream(appConfig) as Handler, requestFor(C, { conversationId: w.chatId }, { query: 'sneaky' }))
      expect(next.firstCall?.args[0]?.statusCode).to.equal(403)
    })
  })

  describe('tool results and reasoning only for the turn’s author', () => {
    it('shows each user their own turns’ tool activity and hides everyone else’s, the owner’s view included', async () => {
      const w = world()
      const chat = w.store.sessions[0]!
      w.store.addMessage(chat, { messageType: 'user_query', content: 'B question', authorUserId: B })
      w.store.addMessage(chat, {
        messageType: 'bot_response',
        content: 'B answer',
        authorUserId: B,
        tools: [{ toolName: 'search', toolResult: B_TOOL }],
        reasoning: [{ content: B_TOOL }],
      })

      const asA = JSON.stringify(await getAs(w, A))
      expect(asA).to.include(A_TOOL)
      expect(asA, 'the owner does not see B’s tool results or reasoning').to.not.include(B_TOOL)

      const asB = JSON.stringify(await getAs(w, B))
      expect(asB).to.include(B_TOOL)
      expect(asB).to.not.include(A_TOOL)

      const asC = JSON.stringify(await getAs(w, C))
      expect(asC).to.not.include(A_TOOL)
      expect(asC).to.not.include(B_TOOL)
      const hidden = (await getAs(w, A)).filter((m) => m.detailsHidden)
      // B's answer cites nothing and carries tool output, so for anyone else it is hidden whole.
      expect(hidden.map((m) => [m.content, m.redaction?.redacted])).to.deep.equal([['', true]])
    })
  })

  describe('the owner grants write', () => {
    const share = async (w: World, who: Types.ObjectId, userIds: Types.ObjectId[], accessLevel?: string) => {
      const next = sinon.stub()
      // Every named user is a member of the org.
      ;(Users.find as unknown as sinon.SinonStub).callsFake(((filter: { _id: { $in: Types.ObjectId[] } }) => {
        const chain: any = { select: () => chain, lean: () => chain, exec: () => Promise.resolve(filter._id.$in.map((id) => ({ _id: id }))) }
        return chain
      }) as never)
      const body = conversationShareParamsSchema.shape.body.parse({ userIds: userIds.map(String), ...(accessLevel ? { accessLevel } : {}) })
      await (controller.shareConversationById(appConfig) as Handler)(
        requestFor(who, { conversationId: w.chatId }, body), new FakeSSEResponse() as never, next as never)
      await settle()
      return next.firstCall?.args[0]
    }
    const level = (w: World, u: Types.ObjectId) =>
      (w.store.sessions[0]!.sharedWith as unknown as Array<{ userId: Types.ObjectId; accessLevel: string }>)
        .find((e) => String(e.userId) === String(u))?.accessLevel

    it('keeps accessLevel through validation, persists it, and changes it on an existing share', async () => {
      const w = world()
      const NEW = oid()
      expect(await share(w, A, [NEW], 'write')).to.equal(undefined)
      expect(level(w, NEW)).to.equal('write')
      expect(await share(w, A, [NEW])).to.equal(undefined)
      expect(level(w, NEW), 'omitted level leaves it alone').to.equal('write')
      await share(w, A, [NEW], 'read')
      expect(level(w, NEW)).to.equal('read')
      await share(w, A, [oid()])
      expect(w.store.sessions[0]!.sharedWith).to.have.length(4)
    })

    it('rejects an unknown level and refuses a writer or reader who tries to share', async () => {
      const w = world()
      expect(() => conversationShareParamsSchema.shape.body.parse({ userIds: [String(oid())], accessLevel: 'admin' })).to.throw()
      for (const who of [B, C]) {
        const err = await share(w, who, [C], 'write')
        expect(err?.statusCode, String(who)).to.equal(404)
      }
      expect(level(w, C)).to.equal('read')
    })
  })

  describe('an agent conversation read by a recipient', () => {
    it('goes through the same redaction as a chat: the owner’s private source is hidden from a recipient, shown to the owner', async () => {
      const w = world()
      const read = async (who: Types.ObjectId) => {
        const res = new FakeSSEResponse()
        const next = sinon.stub()
        await (controller.getAgentConversationById(appConfig) as Handler)(
          requestFor(who, { conversationId: w.agentChatId, agentKey: AGENT_KEY }), res as never, next as never)
        await settle()
        expect(next.firstCall?.args[0], 'GET error').to.equal(undefined)
        return JSON.stringify(res.jsonBody)
      }
      expect(await read(C)).to.not.include(A_CHUNK)
      expect(await read(A)).to.include(A_CHUNK)
    })
  })

  describe('who asked', () => {
    it('labels questions asked by someone else with that user’s name, resolved in the viewer’s org, and never the viewer’s own', async () => {
      const w = world()
      const lookup = (Users.find as unknown as sinon.SinonStub).callsFake(((filter: { _id: { $in: string[] } }) => {
        const names: Record<string, string> = { [String(A)]: 'Asha Rao', [String(B)]: 'Ben Cho' }
        const rows = filter._id.$in.map((id) => ({ _id: id, fullName: names[String(id)] }))
        const chain: any = { select: () => chain, lean: () => chain, exec: () => Promise.resolve(rows) }
        return chain
      }) as never)
      await addAsB(w)

      const asA = await getAs(w, A)
      expect(asA[0]!.askedBy, 'A’s own question').to.equal(undefined)
      expect(asA[2]!.askedBy).to.deep.equal({ userId: String(B), name: 'Ben Cho' })
      const asB = await getAs(w, B)
      expect(asB[0]!.askedBy).to.deep.equal({ userId: String(A), name: 'Asha Rao' })
      expect(asB[2]!.askedBy, 'B’s own question').to.equal(undefined)
      expect(asB.filter((m) => m.messageType === 'bot_response').every((m) => m.askedBy === undefined)).to.equal(true)
      for (const call of lookup.getCalls().filter((c) => c.args[0]?._id)) {
        expect(String(call.args[0].orgId)).to.equal(String(ORG))
      }
    })
  })

  describe('the history the model gets holds only what the requester may read', () => {
    it('gives B’s request A’s turn without A’s unreadable source or tool output', async () => {
      const w = world()
      await addAsB(w)

      const history = JSON.stringify((w.ai.streamCalls[0]!.body as any).previousConversations)
      expect(history).to.include('Everyone knows the office moved')
      expect(history).to.not.include('The merger closes in May')
      expect(history).to.not.include(A_TOOL)
    })

    it('gives A’s next request B’s turn redacted, and B’s own turn on a later request whole', async () => {
      const w = world()
      await addAsB(w)
      w.ai.calls.length = 0

      await streamTurn(w, controller.addMessageStream(appConfig) as StreamHandler, A, { conversationId: w.chatId }, { query: 'A follows up' }, { answer: 'ok' })
      const forA = JSON.stringify((w.ai.streamCalls[0]!.body as any).previousConversations)
      expect(w.ai.streamCalls[0]?.headers.authorization).to.equal('Bearer token-A')
      expect(forA, 'B’s answer rested only on a source A cannot read').to.not.include('Layoffs are planned')
      expect(forA).to.include('B asks about the layoffs')
      expect(forA).to.include('The merger closes in May')

      w.ai.calls.length = 0
      await streamTurn(w, controller.addMessageStream(appConfig) as StreamHandler, B, { conversationId: w.chatId }, { query: 'B again' }, { answer: 'ok' })
      const forB = JSON.stringify((w.ai.streamCalls[0]!.body as any).previousConversations)
      expect(forB).to.include('Layoffs are planned')
      expect(forB).to.not.include('The merger closes in May')
    })

    it('fails closed when the access check is down', async () => {
      const w = world()
      w.ai.resetReplies()
      w.ai.reply(/\/chat\/records\/access$/, 500, {})
      await addAsB(w)

      const history = JSON.stringify((w.ai.streamCalls[0]!.body as any).previousConversations)
      expect(history).to.not.include('The merger closes in May')
    })
  })

  describe('turns from two users keep one ordering and lose nothing', () => {
    it('allocates distinct, increasing seq values to appends racing from different users', async () => {
      const w = world()
      const chat = w.store.sessions[0]!
      const m = (content: string) => ({ messageType: 'user_query' as const, content })
      const [fromB, fromA] = await Promise.all([
        appendMessages(chat._id, ORG, [m('b1'), m('b2')], null, B),
        appendMessages(chat._id, ORG, [m('a1'), m('a2'), m('a3')], null, A),
      ])

      const seqs = [...fromB, ...fromA].map((d) => d.seq)
      expect(new Set(seqs).size, 'no seq handed out twice').to.equal(5)
      expect(fromB.map((d) => d.seq)).to.deep.equal([fromB[0]!.seq, fromB[0]!.seq + 1])
      expect(fromA.map((d) => d.seq)).to.deep.equal([fromA[0]!.seq, fromA[0]!.seq + 1, fromA[0]!.seq + 2])
      const stored = w.messages()
      expect(stored).to.have.length(7)
      expect(stored.map((d) => d.seq)).to.deep.equal([...stored.map((d) => d.seq)].sort((x, y) => x - y))
      expect(stored.filter((d) => String(d.authorUserId) === String(B))).to.have.length(2)
      expect(stored.filter((d) => String(d.authorUserId) === String(A))).to.have.length(3)
    })

    it('refuses a second run on the thread rather than interleaving two answers', async () => {
      const w = world()
      w.store.sessions[0]!.set({ status: 'Inprogress', lastActivityAt: Date.now() })
      const clock = sinon.useFakeTimers({ toFake: ['Date', 'setTimeout'], now: Date.now() })
      const res = new FakeSSEResponse()
      const run = (controller.addMessageStream(appConfig) as StreamHandler)(requestFor(B, { conversationId: w.chatId }, { query: 'too soon' }), res as never)
      await clock.tickAsync(16_000)
      await run
      clock.restore()

      expect(res.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(w.ai.streamCalls).to.deep.equal([])
      expect(w.messages()).to.have.length(2)
    })
  })

  describe('only the turn’s author regenerates it', () => {
    it('refuses the owner regenerating the writer’s answer, and changes nothing', async () => {
      const w = world()
      await addAsB(w)
      const botId = String(w.messages().at(-1)!._id)
      w.ai.calls.length = 0

      const res = new FakeSSEResponse()
      await (controller.regenerateAnswers(appConfig) as StreamHandler)(requestFor(A, { conversationId: w.chatId, messageId: botId }), res as never)
      await settle()

      expect(res.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(w.ai.streamCalls).to.deep.equal([])
      expect(w.messages().at(-1)!.content).to.equal(`Layoffs are planned ${B1}.`)
    })

    it('lets the writer regenerate their own answer as themselves, still as their turn', async () => {
      const w = world()
      await addAsB(w)
      const botId = String(w.messages().at(-1)!._id)
      w.ai.calls.length = 0

      const res = await streamTurn(
        w, controller.regenerateAnswers(appConfig) as StreamHandler, B,
        { conversationId: w.chatId, messageId: botId }, {},
        { answer: `Layoffs are planned, take two ${B1}.`, citations: [bCitation] },
      )

      expect(res.eventsOf('RUN_ERROR')).to.deep.equal([])
      expect(w.ai.streamCalls[0]?.headers.authorization).to.equal('Bearer token-B')
      const last = w.messages().at(-1)!
      expect(last.content).to.equal(`Layoffs are planned, take two ${B1}.`)
      expect(String(last.authorUserId), 'a regenerated answer stays its author’s').to.equal(String(B))
    })

    it('applies the same rules to editing the last question: not the owner on a writer’s turn, not a reader', async () => {
      const w = world()
      await addAsB(w)
      const botId = String(w.messages().at(-1)!._id)
      const askedBefore = w.messages().at(-2)!.content
      w.ai.calls.length = 0

      for (const who of [A, C]) {
        const res = new FakeSSEResponse()
        await (controller.regenerateAnswers(appConfig) as StreamHandler)(
          requestFor(who, { conversationId: w.chatId, messageId: botId }, { query: 'An edited question' }), res as never)
        await settle()
        expect(res.eventsOf('RUN_ERROR'), String(who)).to.have.length(1)
      }
      expect(w.ai.streamCalls).to.deep.equal([])
      expect(w.messages().at(-2)!.content, 'the question was not rewritten').to.equal(askedBefore)
      expect(w.messages().at(-1)!.content).to.equal(`Layoffs are planned ${B1}.`)
    })

    it('lets the writer edit their own last question, and its complete frame holds nothing the writer cannot read', async () => {
      const w = world()
      await addAsB(w)
      const botId = String(w.messages().at(-1)!._id)
      w.ai.calls.length = 0

      const res = await streamTurn(
        w, controller.regenerateAnswers(appConfig) as StreamHandler, B,
        { conversationId: w.chatId, messageId: botId }, { query: 'B asks again' },
        { answer: `Layoffs are planned, edited ${B1}.`, citations: [bCitation] },
      )

      expect(res.eventsOf('RUN_ERROR')).to.deep.equal([])
      expect(w.messages().at(-2)!.content).to.equal('B asks again')
      // The frame is for B: A’s turns in it are redacted for B, so A’s private chunk is not in it.
      expect(res.body).to.not.include(A_CHUNK)
    })

    it('lets the owner regenerate their own last answer, and a reader not at all', async () => {
      const w = world()
      const botId = String(w.messages().at(-1)!._id)
      const asReader = new FakeSSEResponse()
      await (controller.regenerateAnswers(appConfig) as StreamHandler)(requestFor(C, { conversationId: w.chatId, messageId: botId }), asReader as never)
      await settle()
      expect(asReader.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(w.ai.streamCalls).to.deep.equal([])

      const res = await streamTurn(w, controller.regenerateAnswers(appConfig) as StreamHandler, A, { conversationId: w.chatId, messageId: botId }, {}, { answer: 'Owner redo.' })
      expect(res.eventsOf('RUN_ERROR')).to.deep.equal([])
      expect(w.messages().at(-1)!.content).to.equal('Owner redo.')
    })
  })

  describe('error frames and content search', () => {
    it('sends the whole conversation back on an error only when every message in it is the receiver’s own', async () => {
      const { conversationForAuthorOnly } = await import('../../../../src/modules/enterprise_search/utils/viewer-redaction')
      const conversation = { initiator: A, messages: [{ content: 'a' }, { content: 'b', authorUserId: B }] }
      expect(conversationForAuthorOnly(conversation, String(A))).to.equal(undefined)
      expect(conversationForAuthorOnly(conversation, String(B))).to.equal(undefined)
      expect(conversationForAuthorOnly({ initiator: A, messages: [{ content: 'a' }] }, String(A))).to.not.equal(undefined)
    })
  })
})
