import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import * as agui from '../../../../src/modules/enterprise_search/utils/agui'
import { FULL_FRAME_MAX_ROWS } from '../../../../src/modules/enterprise_search/utils/utils'
import { FakeQuery, InMemoryChatStore, oid, settle } from '../controller/chat-test-harness'
import { AGENT_KEY, appConfig, finalAnswer, Flow, flows, ORG, OWNER, Run, RUN, seedConversation, startStream, StreamHandler } from '../controller/streaming-flows'
import { configureVisibility, resetVisibility } from '../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { labelContentHash } from '../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

type Row = Record<string, unknown>

const flow = (name: string): Flow => flows.find((f) => f.name === name) as Flow
const sends = flows.filter((f) => !f.regenerate)

/** The same flow from a client that asks for the turn-only final frame. */
const turnFramed = (base: Flow): Flow => ({
  ...base,
  handler: (c) =>
    ((req: { query: Row }, res: never, next?: never) => {
      req.query = { ...req.query, frame: 'turn' }
      return base.handler(c)(req as never, res, next)
    }) as StreamHandler,
})

/** The same flow on a chat that already holds `count` more rows. */
const longChat = (base: Flow, count: number): Flow => ({
  ...base,
  prepare: (store: InMemoryChatStore) => {
    const prepared = base.prepare(store)
    const session = prepared.existing as NonNullable<typeof prepared.existing>
    for (let i = 0; i < count; i += 1) {
      store.addMessage(session, { messageType: i % 2 === 0 ? 'user_query' : 'bot_response', content: `Row ${i}` })
    }
    return prepared
  },
})

/** A regenerate of an answer whose turn stored a question card first, as an ask turn does. */
const regenerateCardTurn = (base: Flow): Flow => ({
  ...base,
  prepare: (store: InMemoryChatStore) => {
    const { session } = seedConversation(store, base.agent)
    store.addMessage(session, { messageType: 'user_query', content: 'Which region?' })
    const card = store.addMessage(session, {
      messageType: 'tool_call',
      content: '',
      tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: 'EU or US?' }] } }],
    })
    const bot = store.addMessage(session, { messageType: 'bot_response', content: 'Pick a region.' })
    store.writes.length = 0
    const params: Record<string, string> = { conversationId: String(session._id), messageId: String(bot._id) }
    if (base.agent) params.agentKey = 'agent-1'
    return { params, body: {}, existing: session, replacedMessageId: String(bot._id), card: String(card._id) } as never
  },
})

const answerWithCitation = (answer: string): Row => ({
  runId: RUN,
  result: {
    answer,
    citations: [{ content: 'Q3 revenue was 12M.', chunkIndex: 0, citationType: 'vectordb|document', metadata: { recordId: 'r1' } }],
    confidence: 'High',
  },
})

/** Mongoose is connected, so a frame that reads rows back would really read them. */
const connected = (): void => {
  Object.defineProperty(mongoose.connection, 'readyState', { configurable: true, get: () => 1 })
}

const finishWith = async (run: Run, result: Row = finalAnswer('Three themes.')): Promise<void> => {
  run.ai.send('RUN_FINISHED', result)
  run.ai.finish()
  await run.res.ended
  await settle()
}

const finishedConversation = (run: Run): Row => {
  const finished = run.res.eventsOf('RUN_FINISHED')
  expect(finished, 'one final frame').to.have.length(1)
  return (finished[0]?.data.result as { conversation: Row }).conversation
}

const idsOf = (rows: Row[]): string[] => rows.map((m) => String(m._id))

describe('final stream frame', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => {
    sinon.restore()
    delete (mongoose.connection as unknown as { readyState?: number }).readyState
  })

  describe('with frame=turn', () => {
    for (const base of sends) {
      it(`${base.name}: carries only this turn's rows, with the answer's citations, and reads nothing back`, async () => {
        connected()
        const populate = sinon.spy(FakeQuery.prototype, 'populate')
        const run = await startStream(turnFramed(base))
        // The asker can read the cited record, so the final frame shows the answer.
        run.ai.reply(/\/chat\/records\/access$/, 200, { accessible: ['r1'] })

        await finishWith(run, answerWithCitation('Revenue grew.'))

        const conversation = finishedConversation(run)
        const stored = run.messages()
        const turn = stored.slice(-2)
        expect(conversation.frame).to.equal('turn')
        expect(idsOf(conversation.messages as Row[])).to.deep.equal(idsOf(turn))
        expect((conversation.messages as Row[]).map((m) => m.messageType)).to.deep.equal(['user_query', 'bot_response'])
        const [question, answer] = conversation.messages as Row[]
        for (const row of [question, answer]) {
          expect(row).to.not.have.any.keys('seq', 'changeRev', 'sessionId', 'orgId')
        }
        const citation = (answer?.citations as Array<{ citationData?: Row }>)[0]
        expect(citation?.citationData?.content).to.equal('Q3 revenue was 12M.')
        expect(populate.called, 'no read-back of the chat').to.equal(false)
        expect(run.conversation().status).to.equal('Complete')
      })
    }

    it('carries the cards the turn stored between its question and its answer', async () => {
      const run = await startStream(turnFramed(flow('addMessageStream')))
      run.ai.send('CUSTOM', { name: 'ask_user_question', value: { toolData: { name: 'ask_user_question', questions: [{ question: 'EU or US?' }] } } })
      await settle()

      await finishWith(run, { runId: RUN, result: { answer: '', status: 'waiting_input', citations: [] } })

      const conversation = finishedConversation(run)
      expect((conversation.messages as Row[]).map((m) => m.messageType)).to.deep.equal(['user_query', 'tool_call', 'bot_response'])
      expect(idsOf(conversation.messages as Row[])).to.deep.equal(idsOf(run.messages().slice(-3)))
    })

    for (const name of ['regenerateAnswers', 'regenerateAgentAnswers']) {
      it(`${name}: carries the new answer and the ids of the cards it retired`, async () => {
        connected()
        const run = await startStream(turnFramed(regenerateCardTurn(flow(name))))
        const card = (run.prepared as unknown as { card: string }).card

        await finishWith(run, finalAnswer('Both regions grew.'))

        const conversation = finishedConversation(run)
        expect(conversation.frame).to.equal('turn')
        expect(idsOf(conversation.messages as Row[])).to.deep.equal([run.prepared.replacedMessageId])
        expect((conversation.messages as Row[])[0]?.content).to.equal('Both regions grew.')
        expect((conversation.removedIds as unknown[]).map(String)).to.deep.equal([card])
        expect(run.messages().find((m) => String(m._id) === card)?.removedAt, 'the card is retired').to.exist
      })
    }

    it('a regenerate that ends without an answer sends only the failed row in its error frame', async () => {
      const run = await startStream(turnFramed(flow('regenerateAnswers')))
      run.ai.finish()
      await run.res.ended
      await settle()

      const [error] = run.res.eventsOf('RUN_ERROR')
      const conversation = error?.data.conversation as Row
      expect(conversation.frame).to.equal('turn')
      expect(idsOf(conversation.messages as Row[])).to.deep.equal([run.prepared.replacedMessageId])
      expect((conversation.messages as Row[])[0]?.messageType).to.equal('error')
    })
  })

  describe('without frame=turn (a client from before it)', () => {
    it(`carries the newest ${FULL_FRAME_MAX_ROWS} rows, oldest first`, async () => {
      connected()
      const run = await startStream(longChat(flow('addMessageStream'), FULL_FRAME_MAX_ROWS + 5))

      await finishWith(run)

      const conversation = finishedConversation(run)
      expect(conversation).to.not.have.property('frame')
      expect(idsOf(conversation.messages as Row[])).to.deep.equal(idsOf(run.messages().slice(-FULL_FRAME_MAX_ROWS)))
    })

    it('a regenerate error frame carries the rows, not a turn', async () => {
      const run = await startStream(flow('regenerateAnswers'))
      run.ai.finish()
      await run.res.ended
      await settle()

      const conversation = run.res.eventsOf('RUN_ERROR')[0]?.data.conversation as Row
      expect(conversation).to.not.have.property('frame')
      expect(idsOf(conversation.messages as Row[])).to.deep.equal(idsOf(run.messages()))
    })
  })

  describe('a 30,000-row chat', () => {
    const ROWS = 30_000

    it('frame=turn carries one turn', async function () {
      this.timeout(120_000)
      connected()
      const run = await startStream(turnFramed(longChat(flow('addMessageStream'), ROWS)))

      await finishWith(run)

      expect((finishedConversation(run).messages as Row[]).map((m) => m.messageType)).to.deep.equal(['user_query', 'bot_response'])
    })

    it(`a client from before frame=turn still gets the newest ${FULL_FRAME_MAX_ROWS} rows to show`, async function () {
      this.timeout(120_000)
      connected()
      const run = await startStream(longChat(flow('addMessageStream'), ROWS))

      await finishWith(run)

      const rows = finishedConversation(run).messages as Row[]
      expect(rows).to.have.length(FULL_FRAME_MAX_ROWS)
      expect(rows.at(-1)?.content).to.equal('Three themes.')
    })
  })

  describe('a turn whose save committed', () => {
    for (const framed of [true, false]) {
      it(`is never marked failed when its final frame cannot be sent${framed ? '' : ' (no frame=turn)'}`, async () => {
        const real = agui.frameAGUI
        let thrown = false
        sinon.stub(agui, 'frameAGUI').callsFake((type, payload) => {
          if (type === agui.AGUIEventType.RUN_FINISHED && !thrown) {
            thrown = true
            throw new RangeError('Invalid string length')
          }
          return real(type, payload)
        })
        const base = flow('addMessageStream')
        const run = await startStream(framed ? turnFramed(base) : base)

        await finishWith(run, finalAnswer('Saved answer.'))

        expect(thrown).to.equal(true)
        expect(run.conversation().status).to.equal('Complete')
        expect(run.conversation().conversationErrors ?? []).to.have.length(0)
        expect(run.messages().map((m) => m.messageType), 'no error row').to.deep.equal(['user_query', 'bot_response', 'user_query', 'bot_response'])
        expect(run.answer()?.content).to.equal('Saved answer.')
        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        const conversation = finishedConversation(run)
        expect(conversation.frame).to.equal('turn')
        expect(idsOf(conversation.messages as Row[])).to.deep.equal(idsOf(run.messages().slice(-2)))
      })
    }

    it('is still marked failed when the save itself fails', async () => {
      const run = await startStream(turnFramed(flow('addMessageStream')))
      ;(Citation.insertMany as unknown as sinon.SinonStub).rejects(new Error('write failed'))

      await finishWith(run, answerWithCitation('Lost answer.'))

      expect(run.res.eventsOf('RUN_FINISHED')).to.deep.equal([])
      expect(run.res.eventsOf('RUN_ERROR')).to.have.length(1)
      expect(run.conversation().status).to.equal('Failed')
    })
  })

  describe("in an agent chat, another person's earlier answer whose label names the chat's agent", () => {
    const EARLIER = 'The agent found three themes.'
    // Someone the owner shared the chat with asked it: the asker's own answer passes an unsettled check anyway.
    const ASKER = oid()
    let earlier: Row = {}

    /** The chat as its agent left it: a first turn whose answer's label names the agent, then the turn a regenerate replaces. */
    const agentWrote = (base: Flow): Flow => ({
      ...base,
      prepare: (store: InMemoryChatStore) => {
        const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Agent chat', sessionType: 'agent', agentKey: AGENT_KEY, conversationSource: 'agent_chat' })
        store.addMessage(session, { messageType: 'user_query', content: 'What changed?', authorUserId: ASKER })
        const first = store.addMessage(session, { messageType: 'bot_response', content: EARLIER, requestedBy: ASKER })
        const labelVer = '6a0000000000000000000001'
        earlier = {
          _id: String(first._id),
          sessionId: String(session._id),
          orgId: ORG,
          messageType: 'bot_response',
          requestedBy: ASKER,
          content: EARLIER,
          parts: [],
          labelVer,
          provenance: {
            v: 5,
            ver: labelVer,
            contentHash: labelContentHash(EARLIER, []),
            direct: { records: [], vrids: [], nodes: [], saVrids: [], personal: [], agents: [{ key: AGENT_KEY, kind: 'config' }], chat: [], projects: [], marks: [] },
            parents: [],
            complete: true,
            tagset: 1,
            pub: false,
          },
        }
        const params: Record<string, string> = { conversationId: String(session._id), agentKey: AGENT_KEY }
        if (!base.regenerate) {
          store.writes.length = 0
          return { params, body: { query: 'More detail please' }, existing: session }
        }
        store.addMessage(session, { messageType: 'user_query', content: 'And since then?' })
        const bot = store.addMessage(session, { messageType: 'bot_response', content: 'Nothing yet.' })
        store.writes.length = 0
        return { params: { ...params, messageId: String(bot._id) }, body: {}, existing: session, replacedMessageId: String(bot._id) }
      },
    })

    beforeEach(() => {
      configureVisibility({
        labelRule: async () => true,
        labels: async (_org, ids) => new Map(ids.filter((id) => id === earlier._id).map((id) => [id, earlier as never])),
        // As the edition wires it: the agent check calls out as the reader, so a read with no request behind it cannot say.
        canUseAgent: async (reader, key) => (reader.identity === undefined ? undefined : key === AGENT_KEY),
      })
    })
    afterEach(() => {
      resetVisibility()
    })

    const shownEarlier = (conversation: Row): Row | undefined => (conversation.messages as Row[]).find((m) => String(m._id) === earlier._id)

    for (const name of ['addMessageStreamToAgentConversation', 'regenerateAgentAnswers']) {
      it(`${name}: is shown to the owner, who may use the agent, in the final frame: checked as the caller, as a fetch checks it`, async () => {
        connected()
        const run = await startStream(agentWrote(flow(name)))

        await finishWith(run)

        const shown = shownEarlier(finishedConversation(run))
        expect(shown?.content).to.equal(EARLIER)
        expect(shown).to.not.have.property('answerHidden')
      })
    }

    it('regenerateAgentAnswers: is shown to the owner in the error frame too', async () => {
      connected()
      const run = await startStream(agentWrote(flow('regenerateAgentAnswers')))
      run.ai.finish()
      await run.res.ended
      await settle()

      const shown = shownEarlier(run.res.eventsOf('RUN_ERROR')[0]?.data.conversation as Row)
      expect(shown?.content).to.equal(EARLIER)
      expect(shown).to.not.have.property('answerHidden')
    })
  })
})
