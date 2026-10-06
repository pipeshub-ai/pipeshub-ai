import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, turnWorld } from '../helpers/turn-world'

const SOLO = { projectId: undefined, projectVisibility: undefined, isShared: false, sharedWith: [] }
const BODY = { query: 'And the roadmap?', runId: 'client-run-1', clientMessageId: 'k-1', baseSeq: 0, filesShared: true }
const chatPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/)

/** The payload Python got before PH-05, with the client's run id forwarded. */
const legacyPayload = (kind: Kind, conversationId: string, history: unknown[]) => ({
  query: 'And the roadmap?',
  previousConversations: history,
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
  ...(kind === 'agent' ? {} : {}),
})

const inert = ['authorUserId', 'requestedBy', 'inReplyTo']

describe('J-02/AU-01: with the flag off a follow-up behaves as before', () => {
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const) {
    it(`${kind} stream: same frames, same payload except the stored author fields, no lease, no run id`, async () => {
      const w = turnWorld(SOLO, { collab: false })
      const priorHistory = formatPreviousConversations(w.rows(kind) as never)

      const turn = await w.start('A', kind, 'stream', BODY)
      w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Roadmap.' })
      await w.answerStream('Roadmap.')
      await turn.res.ended

      expect(turn.lease).to.equal(undefined)
      const payload = w.s.ai.streamCalls[0]!.body
      expect(payload).to.deep.equal({ ...legacyPayload(kind, w.s.ids[kind], priorHistory), protocol: 'agui' })
      expect(turn.res.headers).to.not.have.property('x-run-id')
      const events = turn.res.events()
      expect(events.map((e) => e.event)).to.deep.equal(['CUSTOM', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_CONTENT', 'RUN_FINISHED'])
      expect(events[0]!.data).to.deep.equal({ type: 'CUSTOM', name: 'conversation_created', value: { conversationId: w.s.ids[kind] } })

      const [question, answer] = w.rows(kind).slice(2)
      expect(String(question!.authorUserId)).to.equal(String(ACTORS.A.userId))
      expect(String(answer!.requestedBy)).to.equal(String(ACTORS.A.userId))
      for (const row of [question!, answer!]) {
        expect(row).to.not.have.any.keys('runId', 'clientMessageId', 'filesShared', 'shareToolResults')
      }
      expect(Object.keys(question!).filter((k) => !inert.includes(k))).to.not.include('runId')
      expect(w.session(kind).activeRun ?? null).to.equal(null)
      expect(w.session(kind).status).to.equal('Complete')
      expect(w.session(kind).rev, 'rev still moves').to.be.greaterThan(0)
    })

    it(`${kind} non-stream: the body run id is forwarded and no run id comes back`, async () => {
      const w = turnWorld(SOLO, { collab: false })
      w.s.ai.reply(chatPath(kind), 200, { answer: 'Roadmap.', citations: [], confidence: 'High' })
      const priorHistory = formatPreviousConversations(w.rows(kind) as never)

      const turn = await w.start('A', kind, 'plain', BODY)

      expect(turn.error).to.equal(undefined)
      const sent = w.s.ai.calls.find((c) => chatPath(kind).test(c.url))!
      expect(sent.body).to.deep.equal(legacyPayload(kind, w.s.ids[kind], priorHistory))
      expect(turn.res.headers).to.not.have.property('x-run-id')
      expect(w.rows(kind).at(-1)).to.not.have.any.keys('runId')
      expect(w.session(kind).activeRun ?? null).to.equal(null)
    })
  }

  it('two sends by the owner at once both proceed: no 409, no baseSeq or duplicate checks', async () => {
    const w = turnWorld(SOLO, { collab: false })
    const [x, y] = await Promise.all([w.start('A', 'chat', 'stream', { clientMessageId: 'same', baseSeq: -5 }), w.start('A', 'chat', 'stream', { clientMessageId: 'same' })])
    expect(x.error).to.equal(undefined)
    expect(y.error).to.equal(undefined)
    expect(x.reached && y.reached).to.equal(true)
    expect(w.s.ai.streamCalls).to.have.length(2)
    expect(w.rows().filter((r) => r.messageType === 'user_query' && 'clientMessageId' in r)).to.have.length(0)
    x.res.disconnect()
    y.res.disconnect()
  })

  it('the history is the rows before the question, as the old slice(0, -1) read gave', async () => {
    const w = turnWorld(SOLO, { collab: false })
    w.s.store.addMessage(w.s.store.session(w.s.ids.chat)!, { messageType: 'tool_call', content: '', tools: [{ toolName: 'ask_user_question', toolResult: {} }] })
    w.s.store.addMessage(w.s.store.session(w.s.ids.chat)!, { messageType: 'error', content: 'earlier failure' })
    const all = w.rows()
    const turn = await w.start('A', 'chat', 'stream')
    expect(w.s.ai.streamCalls[0]!.body.previousConversations).to.deep.equal(formatPreviousConversations(all as never))
    turn.res.disconnect()
  })
})
