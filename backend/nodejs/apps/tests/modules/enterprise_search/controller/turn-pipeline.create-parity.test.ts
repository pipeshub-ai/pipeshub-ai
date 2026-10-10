import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

const BODY = { query: 'Roadmap?', runId: 'client-run-1', clientMessageId: 'k-1', filesShared: true, shareToolResults: true }
const aiPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat\/stream$/ : /\/agent\/agent-1\/chat\/stream$/)
const chatPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/)

/** The payload Python got for a new conversation before PH-05, with the client's run id forwarded. */
const legacyPayload = (kind: Kind, conversationId: string) => ({
  query: 'Roadmap?',
  previousConversations: [],
  recordIds: [],
  filters: {},
  attachments: [],
  modelKey: null,
  modelName: null,
  modelFriendlyName: null,
  reasoningEffort: null,
  timezone: null,
  currentTime: null,
  conversationId,
  runId: 'client-run-1',
  aclVersion: 0,
  chatMode: 'quick',
  ...(kind === 'agent' ? { quickMode: false } : {}),
})

const inert = ['authorUserId', 'requestedBy', 'inReplyTo']
const flagOnly = ['creationKey', 'activeRun', 'clientMessageId', 'filesShared', 'shareToolResults', 'runId']

describe('J-02/AU-01: with the flag off a first send behaves as before', () => {
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const) {
    it(`${kind} stream: headers first, same frames, same payload and rows apart from the stored author fields`, async () => {
      const w = turnWorld({}, { collab: false })
      const turn = await w.startCreate('A', kind, 'stream', BODY)
      w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Roadmap.' })
      await w.answerStream('Roadmap.')
      await turn.res.ended
      await settle()

      const [fresh] = w.created()
      expect(w.handles).to.have.length(0)
      expect(w.s.ai.streamCalls[0]!.url).to.match(aiPath(kind))
      expect(w.s.ai.streamCalls[0]!.body).to.deep.equal({ ...legacyPayload(kind, String(fresh!._id)), protocol: 'agui' })

      const order = w.s.store.writes.filter((write) => write === 'writeHead' || write === 'chatSession.save')
      expect(order, 'the SSE head is written before the session is created').to.deep.equal(['writeHead', 'chatSession.save'])
      expect(turn.res.headers).to.not.have.property('x-run-id')
      expect(Object.keys(turn.res.headers).sort()).to.deep.equal(['access-control-allow-origin', 'cache-control', 'connection', 'content-type', 'x-accel-buffering'])
      const events = turn.res.events()
      expect(events.map((e) => e.event)).to.deep.equal(['CUSTOM', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_CONTENT', 'RUN_FINISHED'])
      expect(events[0]!.data).to.deep.equal({ type: 'CUSTOM', name: 'conversation_created', value: { conversationId: String(fresh!._id), title: 'Roadmap?' } })

      expect(fresh!.creationKey).to.equal(undefined)
      expect(fresh!.activeRun ?? null).to.equal(null)
      expect(fresh).to.include({ status: 'Complete', sessionType: kind })
      const [question, reply] = w.rowsOf(fresh!._id)
      expect(String(question!.authorUserId)).to.equal(String(ACTORS.A.userId))
      expect(String(reply!.requestedBy)).to.equal(String(ACTORS.A.userId))
      for (const row of [question!, reply!]) expect(Object.keys(row).filter((k) => flagOnly.includes(k))).to.deep.equal([])
      expect(Object.keys(question!).filter((k) => inert.includes(k))).to.deep.equal(['authorUserId'])
    })

    it(`${kind} stream: the same clientMessageId twice creates two conversations, and nothing is held`, async () => {
      const w = turnWorld({}, { collab: false })
      const [x, y] = await Promise.all([w.startCreate('A', kind, 'stream', BODY), w.startCreate('A', kind, 'stream', BODY)])
      expect(x.error).to.equal(undefined)
      expect(y.error).to.equal(undefined)
      expect(w.created()).to.have.length(2)
      expect(w.handles).to.have.length(0)
      x.res.disconnect()
      y.res.disconnect()
      await settle(10)
    })

    it(`${kind} non-stream: 201, the body run id is forwarded, no run id comes back`, async () => {
      const w = turnWorld({}, { collab: false })
      w.s.ai.reply(chatPath(kind), 200, { answer: 'Roadmap.', citations: [], confidence: 'High' })
      const turn = await w.startCreate('A', kind, 'plain', BODY)

      expect(turn.error).to.equal(undefined)
      expect(turn.res.statusCode).to.equal(201)
      const [fresh] = w.created()
      const sent = w.s.ai.calls.find((c) => chatPath(kind).test(c.url))!
      expect(sent.body).to.deep.equal(legacyPayload(kind, String(fresh!._id)))
      expect(turn.res.headers).to.not.have.property('x-run-id')
      expect(fresh!.creationKey).to.equal(undefined)
      expect(fresh!.activeRun ?? null).to.equal(null)
      expect(w.handles).to.have.length(0)
      const [question, reply] = w.rowsOf(fresh!._id)
      for (const row of [question!, reply!]) expect(Object.keys(row).filter((k) => flagOnly.includes(k))).to.deep.equal([])
    })
  }
})
