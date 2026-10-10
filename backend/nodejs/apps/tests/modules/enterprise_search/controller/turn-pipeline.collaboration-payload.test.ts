import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ACTORS, ActorName } from '../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../helpers/turn-world'

const NAMES: Record<string, string> = {
  [String(ACTORS.A.userId)]: 'Alice',
  [String(ACTORS.B.userId)]: 'Bob',
  [String(ACTORS.Bt.userId)]: 'Carol',
}
const directory = {
  displayNames: async (_org: string, ids: readonly string[]) => new Map(ids.map((i) => [i, NAMES[i] ?? ''])),
  findByIds: async () => [],
}
const chatPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/)
const answer = (text: string): Record<string, unknown> => ({ answer: text, citations: [], confidence: 'High' })
const SOLO = { projectId: undefined, projectVisibility: undefined, isShared: false, sharedWith: [] }

/** Every string key and value under `value`. */
const strings = (value: unknown, out: string[] = []): string[] => {
  if (typeof value === 'string') out.push(value)
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out))
  else if (value && typeof value === 'object')
    for (const [k, v] of Object.entries(value)) {
      out.push(k)
      strings(v, out)
    }
  return out
}
const expectNoIdentity = (body: Record<string, any>): void => {
  const blob = JSON.stringify({ collaboration: body.collaboration, previousConversations: body.previousConversations })
  for (const a of Object.values(ACTORS)) expect(blob).to.not.include(String(a.userId))
  expect(blob).to.not.match(/@example\.com|userId|authorUserId|requestedBy|orgId/)
}
const addTurn = (w: TurnWorld, kind: Kind, who: ActorName, text: string): void => {
  const doc = w.s.store.session(w.s.ids[kind])!
  w.s.store.addMessage(doc, { messageType: 'user_query', content: text, authorUserId: ACTORS[who].userId })
  w.s.store.addMessage(doc, { messageType: 'bot_response', content: `re: ${text}` })
}
const sentTo = (w: TurnWorld, kind: Kind): Record<string, any> => w.s.ai.calls.find((c) => chatPath(kind).test(c.url))!.body

describe('collaboration block on the AI request (flag on, shared chat)', () => {
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const) {
    describe(kind, () => {
      it('non-stream: B sends; the roster names both, marks B, and rows carry authorRef', async () => {
        const w = turnWorld({}, { deps: { users: directory } })
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))

        const turn = await w.start('B', kind, 'plain')

        expect(turn.error).to.equal(undefined)
        const body = sentTo(w, kind)
        expect(body.collaboration).to.deep.equal({
          participants: [
            { ref: 'participant_1', displayName: 'Alice', isCurrentSender: false },
            { ref: 'participant_2', displayName: 'Bob', isCurrentSender: true },
          ],
          currentSenderRef: 'participant_2',
        })
        expect(body.previousConversations.map((m: any) => m.authorRef)).to.deep.equal(['participant_1', undefined])
        expectNoIdentity(body)
      })

      it('stream: refs are stable across turns and the sender follows the caller', async () => {
        const w = turnWorld({}, { deps: { users: directory } })
        const first = await w.start('B', kind, 'stream', { query: 'b says' })
        const one = w.s.ai.streamCalls[0]!.body
        await w.answerStream('done')
        await first.res.ended

        const second = await w.start('A', kind, 'stream', { query: 'a says' })
        const two = w.s.ai.streamCalls[1]!.body
        second.res.disconnect()

        expect(one.collaboration.currentSenderRef).to.equal('participant_2')
        expect(two.collaboration.currentSenderRef).to.equal('participant_1')
        const refOf = (c: any, name: string) => c.participants.find((p: any) => p.displayName === name).ref
        for (const name of ['Alice', 'Bob']) expect(refOf(two.collaboration, name)).to.equal(refOf(one.collaboration, name))
        const rows = two.previousConversations.filter((m: any) => m.role === 'user_query')
        expect(rows.map((m: any) => [m.content, m.authorRef])).to.deep.equal([
          ['question', 'participant_1'],
          ['b says', 'participant_2'],
        ])
        expectNoIdentity(two)
      })

      it('a third person gets the next ref and legacy rows map to the owner', async () => {
        const w = turnWorld({}, { deps: { users: directory } })
        addTurn(w, kind, 'B', 'from bob')
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))

        await w.start('Bt', kind, 'plain')

        const body = sentTo(w, kind)
        expect(body.collaboration.participants.map((p: any) => [p.ref, p.displayName, p.isCurrentSender])).to.deep.equal([
          ['participant_1', 'Alice', false],
          ['participant_2', 'Bob', false],
          ['participant_3', 'Carol', true],
        ])
        expect(body.collaboration.currentSenderRef).to.equal('participant_3')
      })

      it('sends no email as a name: an unnamed user reads "Participant"', async () => {
        const w = turnWorld({}, { deps: { users: { ...directory, displayNames: async (_o, ids) => new Map(ids.map((i) => [i, '']) ) } } })
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))
        await w.start('B', kind, 'plain')
        const body = sentTo(w, kind)
        expect(body.collaboration.participants.map((p: any) => p.displayName)).to.deep.equal(['Participant', 'Participant'])
        expect(strings(body.collaboration).join('|')).to.not.include('@')
      })

      it('the owner alone in a shared chat sends no collaboration and no authorRef', async () => {
        const w = turnWorld({}, { deps: { users: directory } })
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))
        await w.start('A', kind, 'plain')
        const body = sentTo(w, kind)
        expect(body).to.not.have.property('collaboration')
        expect(JSON.stringify(body.previousConversations)).to.not.include('authorRef')
      })

      it('flag off: a shared chat sends no collaboration and no authorRef', async () => {
        const w = turnWorld({}, { collab: false, deps: { users: directory } })
        addTurn(w, kind, 'B', 'from bob')
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))
        await w.start('A', kind, 'plain')
        const body = sentTo(w, kind)
        expect(body).to.not.have.property('collaboration')
        expect(JSON.stringify(body.previousConversations)).to.not.include('authorRef')
      })

      it('a solo chat (flag on) sends no collaboration', async () => {
        const w = turnWorld(SOLO, { deps: { users: directory } })
        w.s.ai.reply(chatPath(kind), 200, answer('ok'))
        await w.start('A', kind, 'plain')
        expect(sentTo(w, kind)).to.not.have.property('collaboration')
      })

      it('regenerate: the history before the question is labelled and the asker is the sender', async () => {
        const w = turnWorld({}, { questionAuthor: ACTORS.B.userId, deps: { users: directory } })
        const doc = w.s.store.session(w.s.ids[kind])!
        w.s.store.addMessage(doc, { messageType: 'user_query', content: 'second', authorUserId: ACTORS.A.userId })
        const bot = w.s.store.addMessage(doc, { messageType: 'bot_response', content: 'second answer' })

        const turn = await w.startRegenerate('A', kind, {}, String(bot._id))
        const body = w.s.ai.streamCalls[0]!.body
        turn.res.disconnect()

        expect(body.query).to.equal('second')
        expect(body.collaboration.participants.map((p: any) => [p.ref, p.displayName, p.isCurrentSender])).to.deep.equal([
          ['participant_1', 'Bob', false],
          ['participant_2', 'Alice', true],
        ])
        expect(body.previousConversations.map((m: any) => m.authorRef)).to.deep.equal(['participant_1', undefined])
        expectNoIdentity(body)
      })

      it('regenerate with the flag off sends nothing new', async () => {
        const w = turnWorld({}, { collab: false, questionAuthor: ACTORS.B.userId, deps: { users: directory } })
        const turn = await w.startRegenerate('A', kind, {}, w.s.messageIds[kind])
        const body = w.s.ai.streamCalls[0]?.body
        turn.res.disconnect()
        if (body) {
          expect(body).to.not.have.property('collaboration')
          expect(JSON.stringify(body.previousConversations)).to.not.include('authorRef')
        }
      })
    })
  }
})
