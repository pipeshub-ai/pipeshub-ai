import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { MENTION_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.errors'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

const GUEST = 'guest-9'
const guestAgent = { type: 'agent', id: GUEST }
const ownPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat\/stream$/ : /\/agent\/agent-1\/chat\/stream$/)
const guestPath = /\/api\/v1\/agent\/guest-9\/chat\/stream$/
const profiles = { describe: async (_who: unknown, key: string) => (key === GUEST ? { name: 'Joke Buddy', handle: 'joke-buddy' } : undefined) }
const world = (over: { guest?: boolean; agents?: Record<string, unknown>; mentions?: boolean } = {}) =>
  turnWorld(
    {},
    {
      mentions: {
        enabled: over.mentions ?? true,
        guestAgents: over.guest ?? true,
        profiles: profiles as never,
        agents: { canExecute: sinon.stub().resolves(true), isServiceAccount: sinon.stub().resolves(false), ...over.agents } as never,
      },
    },
  )
const bodyOf = (w: TurnWorld, i = 0): Record<string, any> => w.s.ai.streamCalls[i]!.body

describe('guest agent turns (M2)', () => {
  afterEach(() => sinon.restore())

  describe('routing', () => {
    it('a default chat with no agent mention goes to the assistant', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [] })
      expect(w.s.ai.streamCalls[0]!.url).to.match(ownPath('chat'))
      await w.answerStream()
      await turn.res.ended
      expect(w.rows('chat').pop()!.respondingAgentKey).to.equal(undefined)
    })

    it('an agent’s own chat mentioning its own agent stays on that agent and stamps nothing', async () => {
      const w = world()
      const turn = await w.start('B', 'agent', 'stream', { mentions: [{ type: 'agent', id: 'agent-1' }] })
      expect(w.s.ai.streamCalls[0]!.url).to.match(ownPath('agent'))
      await w.answerStream()
      await turn.res.ended
      expect(w.rows('agent').pop()!.respondingAgentKey).to.equal(undefined)
    })

    for (const kind of ['chat', 'agent'] as const) {
      it(`${kind}: a mentioned guest agent answers this turn with the sender’s JWT, the same history and the collaboration payload`, async () => {
        const w = world()
        const turn = await w.start('B', kind, 'stream', { mentions: [guestAgent], tools: ['slack.send'], agentCapabilities: { x: true }, chatMode: 'quick' })
        expect(turn.error).to.equal(undefined)
        const call = w.s.ai.streamCalls[0]!
        expect(call.url).to.match(guestPath)
        expect(call.headers.authorization).to.equal('Bearer token-B')
        const body = bodyOf(w)
        expect(body.previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
        expect(body.collaboration.currentSenderRef).to.equal('participant_2')
        expect(body.collaboration.participants).to.have.length(2)
        // The assistant’s tool selection is not the guest agent’s.
        // The seeded chat is in a project, which narrows tools; the sender's own selection never gets through.
        expect(body.tools ?? []).to.not.include('slack.send')
        expect(body.agentCapabilities).to.equal(undefined)
        expect(body.chatMode).to.equal('quick')
        await w.answerStream('Knock knock')
        await turn.res.ended

        const rows = w.rows(kind)
        const [question, answer] = rows.slice(-2)
        expect(question!.mentions).to.deep.equal([guestAgent])
        expect(answer!.messageType).to.equal('bot_response')
        expect(answer!.respondingAgentKey).to.equal(GUEST)
        expect(String(answer!.requestedBy)).to.equal(String(ACTORS.B.userId))
        // The session keeps its identity.
        expect(w.session(kind).sessionType).to.equal(kind === 'chat' ? 'chat' : 'agent')
        expect(w.session(kind).agentKey).to.equal(kind === 'chat' ? undefined : 'agent-1')
        expect(w.session(kind).activeRun ?? null).to.equal(null)
      })
    }

    it('works in every respond mode: an agent mention always asks for a turn', async () => {
      for (const respondMode of ['smart', 'mention_only', 'always']) {
        const w = turnWorld({ settings: { respondMode } }, { mentions: { guestAgents: true, profiles: profiles as never } })
        const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
        expect(turn.error, respondMode).to.equal(undefined)
        expect(w.s.ai.streamCalls[0]!.url, respondMode).to.match(guestPath)
        await w.answerStream()
        await turn.res.ended
      }
    })

    it('non-streaming follow-ups route and stamp the same way', async () => {
      const w = world()
      w.s.ai.reply(/\/api\/v1\/agent\/guest-9\/chat$/, 200, { answer: 'hi', citations: [], confidence: 'High' })
      const turn = await w.start('B', 'chat', 'plain', { mentions: [guestAgent] })
      expect(turn.error).to.equal(undefined)
      expect(w.s.ai.calls.some((c) => /\/api\/v1\/agent\/guest-9\/chat$/.test(c.url))).to.equal(true)
      expect(w.rows('chat').pop()!.respondingAgentKey).to.equal(GUEST)
    })

    it('a failed guest turn leaves its error row attributed too', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      w.s.ai.send('RUN_ERROR', { runId: 'r', message: 'boom', code: 'llm_error' })
      w.s.ai.finish()
      await settle()
      await turn.res.ended
      expect(w.rows('chat').pop()).to.include({ messageType: 'error', respondingAgentKey: GUEST })
    })
  })

  describe('who may be mentioned', () => {
    it('a service-account agent is refused in a shared chat, before any lease is kept or AI call made', async () => {
      const w = world({ agents: { isServiceAccount: sinon.stub().resolves(true) } })
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      expect(turn.error).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.SA_AGENT_SHARED })
      expect(w.s.ai.streamCalls).to.have.length(0)
      expect(w.session('chat').activeRun ?? null).to.equal(null)
    })

    it('two agents in one message are 422 TOO_MANY_AGENT_MENTIONS', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent, { type: 'agent', id: 'guest-10' }] })
      expect(turn.error).to.include({ statusCode: 422, code: MENTION_ERROR_CODES.TOO_MANY_AGENT_MENTIONS })
      expect(w.s.ai.streamCalls).to.have.length(0)
    })

    it('an agent the sender cannot run is 403 and nothing is sent', async () => {
      const w = world({ agents: { canExecute: sinon.stub().resolves(false) } })
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      expect(turn.error).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.NOT_ALLOWED })
      expect(w.s.ai.streamCalls).to.have.length(0)
    })

    it('with the agent builder flag off only the chat’s own agent is mentionable, as in M1', async () => {
      const w = world({ guest: false })
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      expect(turn.error).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.NOT_ALLOWED })
      expect(turn.error!.publicDetails).to.include({ reason: 'agent_not_in_chat' })
    })

    it('with mentions off the token is ignored and the assistant answers', async () => {
      const w = world({ mentions: false })
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      expect(w.s.ai.streamCalls[0]!.url).to.match(ownPath('chat'))
      await w.answerStream()
      await turn.res.ended
      expect(w.rows('chat').pop()!.respondingAgentKey).to.equal(undefined)
    })
  })

  describe('readiness', () => {
    it('checks the guest agent for the sender and answers with the existing setup-required error', async () => {
      const w = world()
      w.readiness.check.resolves({ status: 'blocked', toolsets: ['slack'] })
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      expect(w.readiness.check.calledOnceWith({ orgId: String(ACTORS.B.orgId), userId: String(ACTORS.B.userId) }, GUEST)).to.equal(true)
      expect(turn.error).to.include({ statusCode: 412, code: 'CONNECTOR_SETUP_REQUIRED' })
      expect(w.s.ai.streamCalls).to.have.length(0)
    })

    it('the chat’s own agent is not checked when a guest answers', async () => {
      const w = world()
      const turn = await w.start('B', 'agent', 'stream', { mentions: [guestAgent] })
      expect(turn.error).to.equal(undefined)
      expect(w.readiness.check.args.map((a) => a[1])).to.deep.equal([GUEST])
      await w.answerStream()
      await turn.res.ended
    })
  })

  describe('no chains', () => {
    it('text a guest agent writes is never parsed as a mention, in its row or in the next turn', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Ask <@agent:guest-10> and @assistant too')
      await turn.res.ended
      const answer = w.rows('chat').pop()!
      expect(answer.mentions ?? []).to.deep.equal([])

      const next = await w.start('B', 'chat', 'stream', { query: 'thanks' })
      expect(w.s.ai.streamCalls[1]!.url).to.match(ownPath('chat'))
      expect(bodyOf(w, 1).mentions ?? []).to.deep.equal([])
      w.s.ai.finish()
      await settle()
      void next
    })

    it('the guest request carries no agent mention of its own and the history attributes its earlier answer by handle', async () => {
      const w = world()
      const first = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Knock knock')
      await first.res.ended
      const second = await w.start('A', 'chat', 'stream', { query: 'who is there', mentions: [guestAgent] })
      const sent = bodyOf(w, 1)
      expect(sent.mentions).to.deep.equal([{ type: 'agent', ref: 'agent:self' }])
      const answer = sent.previousConversations.find((m: any) => m.content === 'Knock knock')
      expect(answer.agentRef).to.equal('agent:joke-buddy')
      w.s.ai.finish()
      await settle()
      void second
    })

    it('an agent the caller cannot read is attributed as agent:other, never by its key or name', async () => {
      const w = turnWorld({}, { mentions: { guestAgents: true, profiles: { describe: async () => undefined } as never } })
      const first = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Knock knock')
      await first.res.ended
      await w.start('A', 'chat', 'stream', { query: 'again' })
      const answer = bodyOf(w, 1).previousConversations.find((m: any) => m.content === 'Knock knock')
      expect(answer.agentRef).to.equal('agent:other')
      expect(JSON.stringify(bodyOf(w, 1))).to.not.include(GUEST)
      w.s.ai.finish()
      await settle()
    })
  })

  describe('first send', () => {
    it('a first send that mentions an agent streams from that agent and stamps the answer', async () => {
      const w = world()
      const turn = await w.startCreate('A', 'chat', 'stream', { mentions: [guestAgent], clientMessageId: 'c1' })
      expect(turn.error).to.equal(undefined)
      expect(w.s.ai.streamCalls[0]!.url).to.match(guestPath)
      expect(w.s.ai.streamCalls[0]!.headers.authorization).to.equal('Bearer token-A')
      expect(bodyOf(w).tools).to.equal(undefined)
      await w.answerStream('Knock knock')
      await turn.res.ended
      const [conversation] = w.created()
      expect(conversation!.sessionType).to.equal('chat')
      expect(conversation!.agentKey).to.equal(undefined)
      expect(w.rowsOf(conversation!._id).pop()).to.include({ messageType: 'bot_response', respondingAgentKey: GUEST })
    })

    it('an agent chat’s first send mentioning another agent also routes to it, and its own agent does not', async () => {
      const w = world()
      const turn = await w.startCreate('A', 'agent', 'stream', { mentions: [guestAgent] })
      expect(w.s.ai.streamCalls[0]!.url).to.match(guestPath)
      await w.answerStream()
      await turn.res.ended
      expect(w.created()[0]!.agentKey).to.equal('agent-1')
      const own = world()
      await own.startCreate('A', 'agent', 'stream', { mentions: [{ type: 'agent', id: 'agent-1' }] })
      expect(own.s.ai.streamCalls[0]!.url).to.match(/\/agent\/agent-1\/chat\/stream$/)
    })

    it('two agents or an agent the sender cannot run is refused before anything is created', async () => {
      const two = world()
      const t2 = await two.startCreate('A', 'chat', 'stream', { mentions: [guestAgent, { type: 'agent', id: 'guest-10' }] })
      expect(t2.error).to.include({ statusCode: 422, code: MENTION_ERROR_CODES.TOO_MANY_AGENT_MENTIONS })
      expect(two.created()).to.have.length(0)
      const no = world({ agents: { canExecute: sinon.stub().resolves(false) } })
      const t3 = await no.startCreate('A', 'chat', 'stream', { mentions: [guestAgent] })
      expect(t3.error).to.include({ statusCode: 403 })
      expect(no.created()).to.have.length(0)
      expect(no.s.ai.streamCalls).to.have.length(0)
    })

    it('readiness is checked before the chat is created', async () => {
      const w = world()
      w.readiness.check.resolves({ status: 'blocked', toolsets: ['slack'] })
      const turn = await w.startCreate('A', 'chat', 'stream', { mentions: [guestAgent] })
      expect(turn.error).to.include({ code: 'CONNECTOR_SETUP_REQUIRED' })
      expect(w.created()).to.have.length(0)
    })
  })

  describe('resume', () => {
    it('an answer to a card a guest agent asked goes back to that agent', async () => {
      const w = world()
      const card = w.parkCard('chat', 'B', { respondingAgentKey: GUEST })
      const turn = await w.start('B', 'chat', 'stream', { query: 'User selections: A', resume: { toolCallMessageId: String(card._id) } })
      expect(turn.error).to.equal(undefined)
      expect(w.s.ai.streamCalls[0]!.url).to.match(guestPath)
      w.s.ai.finish()
      await settle()
    })
  })

  describe('colleagues outside the chat', () => {
    it('a follow-up mentioning one reports them in the first frame, so the sender can be offered to add them', async () => {
      const w = world()
      const outsider = String(ACTORS.D.userId)
      const turn = await w.start('B', 'chat', 'stream', { mentions: [{ type: 'user', id: outsider }, guestAgent] })
      expect(turn.error).to.equal(undefined)
      expect(turn.res.events()[0]!.data.value.nonParticipants).to.deep.equal([outsider])
      w.s.ai.finish()
      await settle()
    })

    it('a follow-up that mentions only people in the chat carries no such field', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [{ type: 'user', id: String(ACTORS.A.userId) }, { type: 'assistant', id: 'self' }] })
      expect(turn.res.events()[0]!.data.value).to.not.have.property('nonParticipants')
      w.s.ai.finish()
      await settle()
    })
  })

  describe('the final frame names the answering agent', () => {
    const finalOf = (turn: any): Record<string, any> => turn.res.eventsOf('RUN_FINISHED').at(-1).data.result

    it('a follow-up turn’s RUN_FINISHED carries respondingAgent, as the feed does, on the result and on the saved answer', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Knock knock')
      await turn.res.ended
      const result = finalOf(turn)
      expect(result.respondingAgent).to.deep.equal({ key: GUEST, name: 'Joke Buddy', handle: 'joke-buddy' })
      const answer = result.conversation.messages.find((m: any) => m.messageType === 'bot_response' && m.content === 'Knock knock')
      expect(answer.respondingAgent).to.deep.equal(result.respondingAgent)
    })

    it('a first send’s does too, and an agent the caller cannot read is just its key', async () => {
      const w = turnWorld({}, { mentions: { guestAgents: true, profiles: { describe: async () => undefined } as never } })
      const turn = await w.startCreate('A', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Knock knock')
      await turn.res.ended
      expect(finalOf(turn).respondingAgent).to.deep.equal({ key: GUEST })
    })

    it('an assistant turn and the chat’s own agent carry none', async () => {
      const w = world()
      const plain = await w.start('B', 'chat', 'stream', { mentions: [] })
      await w.answerStream()
      await plain.res.ended
      expect(finalOf(plain)).to.not.have.property('respondingAgent')
      const own = await w.start('B', 'agent', 'stream', { mentions: [{ type: 'agent', id: 'agent-1' }] })
      await w.answerStream()
      await own.res.ended
      expect(finalOf(own)).to.not.have.property('respondingAgent')
    })
  })

  describe('regenerating a guest answer', () => {
    const answered = async (w: TurnWorld) => {
      const turn = await w.start('B', 'chat', 'stream', { mentions: [guestAgent] })
      await w.answerStream('Knock knock')
      await turn.res.ended
      return String(w.rows('chat').find((r) => r.messageType === 'bot_response' && r.content === 'Knock knock')!._id)
    }

    it('re-runs on the same agent with the sender’s token, keeps the attribution and the answer’s place', async () => {
      const w = world()
      const id = await answered(w)
      const before = w.rows('chat').length
      const turn = await w.startRegenerate('B', 'chat', {}, id)
      expect(turn.error).to.equal(undefined)
      const call = w.s.ai.streamCalls.at(-1)!
      expect(call.url).to.match(guestPath)
      expect(call.headers.authorization).to.equal('Bearer token-B')
      expect(call.body.collaboration).to.not.equal(undefined)
      expect(call.body.tools ?? []).to.not.include('slack.send')
      await w.answerStream('Better joke')
      await turn.res.ended
      const rows = w.rows('chat')
      expect(rows).to.have.length(before)
      const replaced = rows.find((r) => String(r._id) === id)!
      expect(replaced).to.include({ content: 'Better joke', respondingAgentKey: GUEST })
      expect(String(replaced.requestedBy)).to.equal(String(ACTORS.B.userId))
    })

    it('is refused with the mention error when the sender can no longer run the agent, and nothing changes', async () => {
      const canExecute = sinon.stub().resolves(true)
      const w = world({ agents: { canExecute } })
      const id = await answered(w)
      canExecute.resolves(false)
      const calls = w.s.ai.streamCalls.length
      const turn = await w.startRegenerate('B', 'chat', {}, id)
      expect(turn.error).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.NOT_ALLOWED })
      expect(w.s.ai.streamCalls).to.have.length(calls)
      expect(w.rows('chat').find((r) => String(r._id) === id)).to.include({ content: 'Knock knock', respondingAgentKey: GUEST })
      expect(w.session('chat').activeRun ?? null).to.equal(null)
    })

    it('is refused when the agent is a service account and the chat is now shared', async () => {
      const isServiceAccount = sinon.stub().resolves(false)
      const w = world({ agents: { isServiceAccount } })
      const id = await answered(w)
      isServiceAccount.resolves(true)
      const turn = await w.startRegenerate('B', 'chat', {}, id)
      expect(turn.error).to.include({ statusCode: 403, code: MENTION_ERROR_CODES.SA_AGENT_SHARED })
      expect(w.rows('chat').find((r) => String(r._id) === id)).to.include({ content: 'Knock knock' })
    })

    it('is the setup error when the agent’s tools are not connected, and 503 when the directory is down', async () => {
      const canExecute = sinon.stub().resolves(true)
      const w = world({ agents: { canExecute } })
      const id = await answered(w)
      w.readiness.check.resolves({ status: 'blocked', toolsets: ['slack'] })
      expect((await w.startRegenerate('B', 'chat', {}, id)).error).to.include({ statusCode: 412 })
      w.readiness.check.resolves({ status: 'ready' })
      canExecute.resolves('unavailable')
      expect((await w.startRegenerate('B', 'chat', {}, id)).error).to.include({ statusCode: 503 })
    })

    it('an answer the assistant wrote still regenerates on the assistant', async () => {
      const w = world()
      const turn = await w.start('B', 'chat', 'stream', { mentions: [] })
      await w.answerStream('plain')
      await turn.res.ended
      const id = String(w.rows('chat').find((r) => r.content === 'plain')!._id)
      const again = await w.startRegenerate('B', 'chat', {}, id)
      expect(again.error).to.equal(undefined)
      expect(w.s.ai.streamCalls.at(-1)!.url).to.match(ownPath('chat'))
      w.s.ai.finish()
      await settle()
    })
  })
})
