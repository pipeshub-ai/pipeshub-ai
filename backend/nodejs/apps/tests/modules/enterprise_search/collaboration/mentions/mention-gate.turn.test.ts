import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Kind, Mode, TurnWorld, turnWorld } from '../../helpers/turn-world'
import { agent, assistant, idOf, team, user } from './mention-world'
import { TEAM_WRITE } from '../../helpers/conversation-world'

const KINDS: Kind[] = ['chat', 'agent']
const MODES: Mode[] = ['stream', 'plain']
const aiBody = (w: TurnWorld): Record<string, any> => (w.s.ai.streamCalls[0] ?? w.s.ai.calls[0])!.body
const lastUserRow = (w: TurnWorld, kind: Kind) => w.rows(kind).filter((r) => r.messageType === 'user_query').at(-1)!
const nothingStarted = (w: TurnWorld, kind: Kind): void => {
  expect(w.handles, 'no lease').to.have.length(0)
  expect(w.readiness.check.called, 'no readiness check').to.equal(false)
  expect(w.s.ai.calls, 'no AI call').to.deep.equal([])
  expect(w.s.ai.streamCalls, 'no AI stream').to.deep.equal([])
  expect(w.rows(kind), 'nothing stored').to.have.length(2)
  expect(w.session(kind).activeRun ?? null).to.equal(null)
}

describe('mention gate inside runLease (PR-10.4)', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    for (const mode of MODES) {
      describe(`${kind} / ${mode}`, () => {
        it('PH10-10: smart, only people and teams mentioned: 422 MESSAGE_IS_NOTE before the lease, readiness, storage and the AI', async () => {
          const w = turnWorld({}, { mentions: {} })
          const turn = await w.start('B', kind, mode, { mentions: [user('C'), team(TEAM_WRITE)] })
          expect(turn.reached).to.equal(false)
          expect(turn.error).to.include({ statusCode: 422, code: 'MESSAGE_IS_NOTE' })
          nothingStarted(w, kind)
        })

        it('mention_only: a message mentioning nobody is a note too; the assistant makes it a question', async () => {
          const w = turnWorld({ settings: { respondMode: 'mention_only' } }, { mentions: {} })
          const turn = await w.start('B', kind, mode)
          expect(turn.error).to.include({ statusCode: 422, code: 'MESSAGE_IS_NOTE' })
          nothingStarted(w, kind)
          const asked = await w.start('B', kind, mode, { mentions: [user('C'), assistant] })
          expect(asked.reached).to.equal(true)
          expect(lastUserRow(w, kind).mentions).to.deep.equal([{ type: 'user', id: idOf('C') }, { type: 'assistant', id: 'self' }])
          expect(aiBody(w).mentions.map((m: { type: string }) => m.type)).to.deep.equal(['participant', 'agent'])
          expect(aiBody(w).mentions[1]).to.deep.equal({ type: 'agent', ref: 'agent:self' })
        })

        it('smart with no mentions is the unchanged turn: no mentions field on the row or in the AI payload', async () => {
          const w = turnWorld({}, { mentions: {} })
          const turn = await w.start('B', kind, mode)
          expect(turn.reached).to.equal(true)
          expect(lastUserRow(w, kind)).to.not.have.property('mentions')
          expect(aiBody(w)).to.not.have.property('mentions')
        })

        it('a token in the text with no mentions entry mentions nobody: stored and sent inert, only the array is stored', async () => {
          const w = turnWorld({}, { mentions: {} })
          const turn = await w.start('B', kind, mode, { query: `<@user:${idOf('C')}> and <@assistant:self> what now?`, mentions: [assistant] })
          expect(turn.reached).to.equal(true)
          const row = lastUserRow(w, kind)
          expect(row.content).to.equal(`<\\@user:${idOf('C')}> and <@assistant:self> what now?`)
          expect(row.mentions).to.deep.equal([{ type: 'assistant', id: 'self' }])
          expect(aiBody(w).query).to.equal(row.content)
          expect(aiBody(w).mentions).to.deep.equal([{ type: 'agent', ref: 'agent:self' }])
        })

        it('a typed reserved alias is the only thing inferred from the text, and is stored as an assistant mention', async () => {
          const w = turnWorld({ settings: { respondMode: 'mention_only' } }, { mentions: {} })
          const turn = await w.start('B', kind, mode, { query: '@PipesHub summarize the thread' })
          expect(turn.reached).to.equal(true)
          expect(lastUserRow(w, kind).mentions).to.deep.equal([{ type: 'assistant', id: 'self' }])
          const other = await w.start('B', kind, mode, { query: '@carol please look' })
          expect(other.error).to.include({ code: 'MESSAGE_IS_NOTE' })
        })

        it('always answers human-only messages and still stores their validated mentions; the AI payload carries them as roster refs only', async () => {
          const w = turnWorld({ settings: { respondMode: 'always' } }, { mentions: {} })
          const turn = await w.start('B', kind, mode, { mentions: [user('C'), user('C')] })
          expect(turn.reached).to.equal(true)
          expect(lastUserRow(w, kind).mentions).to.deep.equal([{ type: 'user', id: idOf('C') }])
          const sent = aiBody(w)
          expect(sent.mentions).to.have.length(1)
          expect(sent.mentions[0]).to.have.keys('type', 'ref')
          expect(sent.mentions[0].type).to.equal('participant')
          expect(sent.mentions[0].ref).to.match(/^participant_\d+$/)
          expect(JSON.stringify(sent), 'no user id crosses').to.not.include(idOf('C'))
        })

        it('MN-05 shape: another org’s user or a foreign team is 400 MENTION_NOT_ALLOWED before any lease', async () => {
          const w = turnWorld({}, { mentions: {} })
          for (const bad of [user('E'), team('not-ours')]) {
            const turn = await w.start('B', kind, mode, { mentions: [assistant, bad] })
            expect(turn.error).to.include({ statusCode: 400, code: 'MENTION_NOT_ALLOWED' })
            expect(turn.error).to.have.nested.property('publicDetails.mentionIndex', 1)
          }
          nothingStarted(w, kind)
        })

        it('MN-12: an org colleague outside the chat does not stop the turn', async () => {
          const w = turnWorld({}, { mentions: {} })
          const turn = await w.start('B', kind, mode, { mentions: [assistant, user('D')] })
          expect(turn.reached).to.equal(true)
          expect(lastUserRow(w, kind).mentions).to.deep.equal([{ type: 'assistant', id: 'self' }, { type: 'user', id: idOf('D') }])
        })

        it('a duplicate clientMessageId is still 409 DUPLICATE_MESSAGE first', async () => {
          const w = turnWorld({}, { mentions: {} })
          w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'q', authorUserId: w.s.store.session(w.s.ids[kind])!.userId, clientMessageId: 'dup' })
          const turn = await w.start('A', kind, mode, { clientMessageId: 'dup', mentions: [user('B')] })
          expect(turn.error).to.include({ statusCode: 409, code: 'DUPLICATE_MESSAGE' })
        })
      })
    }
  }

  describe('agent conversations', () => {
    it('MN-05: the agent token is checked for the sender and 403s when they cannot run it', async () => {
      const w = turnWorld({}, { mentions: { agents: { canExecute: sinon.stub().resolves(false), isServiceAccount: async () => false } } })
      const turn = await w.start('B', 'agent', 'stream', { mentions: [agent()] })
      expect(turn.error).to.include({ statusCode: 403, code: 'MENTION_NOT_ALLOWED' })
      nothingStarted(w, 'agent')
    })

    it('MN-06: a service-account agent in this shared chat is 403 MENTION_SA_AGENT_SHARED', async () => {
      const w = turnWorld({}, { mentions: { agents: { canExecute: async () => true, isServiceAccount: async () => true } } })
      const turn = await w.start('B', 'agent', 'stream', { mentions: [agent()] })
      expect(turn.error).to.include({ statusCode: 403, code: 'MENTION_SA_AGENT_SHARED' })
      nothingStarted(w, 'agent')
    })

    it('503 MENTION_DIRECTORY_UNAVAILABLE when the agent directory cannot answer, but an alias still works', async () => {
      const w = turnWorld({}, { mentions: { agents: { canExecute: async () => 'unavailable', isServiceAccount: async () => false } } })
      const down = await w.start('B', 'agent', 'stream', { mentions: [agent()] })
      expect(down.error).to.include({ statusCode: 503, code: 'MENTION_DIRECTORY_UNAVAILABLE' })
      nothingStarted(w, 'agent')
      const alias = await w.start('B', 'agent', 'stream', { mentions: [assistant] })
      expect(alias.reached).to.equal(true)
    })

    it('the own agent runs the turn with the agent mention stored', async () => {
      const w = turnWorld({}, { mentions: {} })
      const turn = await w.start('B', 'agent', 'stream', { mentions: [agent()] })
      expect(turn.reached).to.equal(true)
      expect(lastUserRow(w, 'agent').mentions).to.deep.equal([{ type: 'agent', id: 'agent-1' }])
    })

    it('a human-only message to an agent chat is a note and never reaches the readiness check', async () => {
      const w = turnWorld({}, { mentions: {} })
      const turn = await w.start('B', 'agent', 'stream', { mentions: [user('C')] })
      expect(turn.error).to.include({ code: 'MESSAGE_IS_NOTE' })
      expect(w.readiness.check.called).to.equal(false)
    })
  })

  it('a resume answers a card and is never a note, whatever the respond mode', async () => {
    const w = turnWorld({ settings: { respondMode: 'mention_only' } }, { mentions: {} })
    const card = w.parkCard('chat', 'B')
    const turn = await w.start('B', 'chat', 'stream', { resume: { toolCallMessageId: String(card._id) }, query: 'User selections: a' })
    expect(turn.error).to.equal(undefined)
    expect(turn.reached).to.equal(true)
  })

  describe('flags off: behaviour is unchanged (parity)', () => {
    const body = { query: 'Same question', mentions: [{ type: 'user', id: 'whatever' }, assistant], clientMessageId: 'k1' }
    const control = { query: 'Same question', clientMessageId: 'k1' }
    const strip = (row: Record<string, any>) => {
      const { _id, sessionId, createdAt, updatedAt, runId, inReplyTo, ...rest } = row
      return rest
    }

    for (const [label, options] of [
      ['mentions off', { mentions: { enabled: false } }],
      ['collaborative chats off', { mentions: {}, collab: false }],
      ['the gate not wired at all', {}],
    ] as const) {
      for (const kind of KINDS) {
        it(`${label}, ${kind}: the mentions field is ignored: same rows, same AI payload, no 4xx even in mention_only`, async () => {
          const sent = turnWorld({ settings: { respondMode: 'mention_only' } }, options)
          const turn = await sent.start('A', kind, 'stream', body)
          expect(turn.error).to.equal(undefined)
          expect(turn.reached).to.equal(true)
          const plain = turnWorld({ settings: { respondMode: 'mention_only' } }, options)
          await plain.start('A', kind, 'stream', control)
          expect(lastUserRow(sent, kind)).to.not.have.property('mentions')
          expect(strip(lastUserRow(sent, kind))).to.deep.equal(strip(lastUserRow(plain, kind)))
          const [a, b] = [aiBody(sent), aiBody(plain)]
          expect(a).to.not.have.property('mentions')
          const { runId: _ra, conversationId: _ca, ...restA } = a
          const { runId: _rb, conversationId: _cb, ...restB } = b
          expect(restA).to.deep.equal(restB)
        })
      }
    }
  })
})
