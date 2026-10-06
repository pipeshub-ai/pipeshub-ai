import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ACTORS, TEAM_READ, TEAM_WRITE } from '../../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../../helpers/turn-world'
import { agent, assistant, idOf, newId, team, user } from './mention-world'

const KINDS: Kind[] = ['chat', 'agent']
const note = (over: Record<string, unknown> = {}) => ({ query: 'FYI Carol, see above', mentions: [user('C')], clientMessageId: 'note-1', ...over })
const post = (w: TurnWorld, who: 'A' | 'B' | 'C' | 'D' | 'E', kind: Kind, body: Record<string, unknown> = note()) => w.request(who, kind, 'POST', '/notes', body)
const code = (out: { error?: { code?: string; statusCode?: number } }) => out.error?.code
const nothingRan = (w: TurnWorld): void => {
  expect(w.handles, 'no lease taken').to.have.length(0)
  expect(w.readiness.check.called, 'readiness not consulted').to.equal(false)
  expect(w.s.ai.calls, 'no AI backend call').to.deep.equal([])
  expect(w.s.ai.streamCalls, 'no AI stream').to.deep.equal([])
}

describe('POST .../notes (PR-10.4 note path)', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    describe(kind, () => {
      it('MN-07: a note mentioning a participant is stored with a seq, takes no lease and calls no AI', async () => {
        const w = turnWorld({}, { mentions: {} })
        const before = w.session(kind)
        const out = await post(w, 'B', kind)
        expect(out.status).to.equal(201)
        const stored = w.rows(kind).at(-1)!
        expect(stored).to.include({ messageType: 'note', content: 'FYI Carol, see above', clientMessageId: 'note-1' })
        expect(stored.mentions).to.deep.equal([{ type: 'user', id: idOf('C') }])
        expect(String(stored.authorUserId)).to.equal(idOf('B'))
        expect(stored.seq).to.equal(3)
        expect(stored).to.not.have.property('runId')
        expect(out.body).to.deep.include({ duplicate: false })
        expect((out.body as any).note).to.include({ id: String(stored._id), seq: 3, messageType: 'note', authorUserId: idOf('B') })
        const after = w.session(kind)
        expect(after.rev - (before.rev ?? 0), 'rev advanced once').to.equal(1)
        expect(after.nextSeq).to.equal(3)
        expect(after.activeRun ?? null).to.equal(null)
        expect(after.status).to.equal(before.status)
        expect(after.lastActivityAt).to.be.at.least(before.lastActivityAt)
        nothingRan(w)
      })

      it('the feed hands the note to the other participants with its author', async () => {
        const w = turnWorld({}, { mentions: {} })
        await post(w, 'B', kind)
        const feed = await w.request('C', kind, 'GET', '/feed?afterSeq=2')
        const messages = (feed.body as any).messages
        expect(messages).to.have.length(1)
        expect(messages[0]).to.include({ messageType: 'note', seq: 3 })
        expect(messages[0].author.userId).to.equal(idOf('B'))
        expect(messages[0].mentions).to.deep.equal([{ type: 'user', id: idOf('C') }])
        expect((feed.body as any).rev).to.equal(w.session(kind).rev)
      })

      it('MN-10: a note lands while someone else’s run streams, after that run’s question and before its answer', async () => {
        const w = turnWorld({}, { mentions: {} })
        const run = await w.start('A', kind, 'stream', { query: 'long question' })
        expect(run.reached).to.equal(true)
        const leased = w.session(kind).activeRun
        const out = await post(w, 'B', kind)
        expect(out.status).to.equal(201)
        expect(w.session(kind).activeRun.runId, 'the run keeps its lease').to.equal(leased.runId)
        expect(w.handles).to.have.length(1)
        await w.answerStream('done')
        await run.res.ended
        const rows = w.rows(kind)
        expect(rows.map((r) => r.messageType)).to.deep.equal(['user_query', 'bot_response', 'user_query', 'note', 'bot_response'])
        expect(rows.map((r) => r.seq)).to.deep.equal([1, 2, 3, 4, 5])
        expect(w.s.ai.streamCalls[0]!.body.previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
      })

      it('MN-10 (next run): the note reaches the AI backend as a note with its author ref and mentions as refs, no ids', async () => {
        const w = turnWorld({}, { mentions: {} })
        const run = await w.start('A', kind, 'stream', { query: 'long question' })
        await post(w, 'B', kind)
        await w.answerStream('done')
        await run.res.ended
        const next = await w.start('A', kind, 'stream', { query: 'what did Bob say?' })
        expect(next.reached).to.equal(true)
        const sent = w.s.ai.streamCalls.at(-1)!.body
        const noteRow = sent.previousConversations.find((m: any) => m.role === 'note')
        expect(noteRow).to.include({ content: 'FYI Carol, see above' })
        expect(noteRow.authorRef).to.match(/^participant_\d+$/)
        expect(noteRow.mentions).to.have.length(1)
        expect(noteRow.mentions[0]).to.have.keys('type', 'ref')
        expect(noteRow.mentions[0].type).to.equal('participant')
        const labels = sent.collaboration.participants.map((p: any) => p.ref)
        expect(labels).to.include.members([noteRow.authorRef, noteRow.mentions[0].ref])
        expect(JSON.stringify(sent), 'no user id crosses').to.not.include(idOf('C'))
        expect(sent.previousConversations.map((m: any) => m.role)).to.deep.equal(['user_query', 'bot_response', 'user_query', 'note', 'bot_response'])
      })

      it('PH10-09: the same clientMessageId twice stores one note and answers with the first', async () => {
        const w = turnWorld({}, { mentions: {} })
        const first = await post(w, 'B', kind)
        const second = await post(w, 'B', kind)
        expect([first.status, second.status]).to.deep.equal([201, 200])
        expect((second.body as any).duplicate).to.equal(true)
        expect((second.body as any).note.id).to.equal((first.body as any).note.id)
        expect(w.rows(kind).filter((r) => r.messageType === 'note')).to.have.length(1)
        expect(w.session(kind).nextSeq).to.equal(3)
      })

      it('the id is per author: another participant can use the same clientMessageId', async () => {
        const w = turnWorld({}, { mentions: {} })
        await post(w, 'B', kind)
        expect((await post(w, 'A', kind, note({ mentions: [user('B')] }))).status).to.equal(201)
        expect(w.rows(kind).filter((r) => r.messageType === 'note')).to.have.length(2)
      })

      it('an id already used by a question of the same author is a duplicate send (409)', async () => {
        const w = turnWorld({}, { mentions: {} })
        w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'q', authorUserId: ACTORS.B.userId, clientMessageId: 'note-1' })
        const out = await post(w, 'B', kind)
        expect(out.error?.statusCode).to.equal(409)
        expect(code(out)).to.equal('DUPLICATE_MESSAGE')
      })

      it('a team that is a principal of the chat can be mentioned', async () => {
        const w = turnWorld({}, { mentions: {} })
        expect((await post(w, 'B', kind, note({ mentions: [team(TEAM_WRITE), team(TEAM_READ)] }))).status).to.equal(201)
        expect(w.rows(kind).at(-1)!.mentions).to.deep.equal([{ type: 'team', id: TEAM_WRITE }, { type: 'team', id: TEAM_READ }])
      })

      it('a participant reached through a team can be mentioned', async () => {
        const w = turnWorld({}, { mentions: {} })
        expect((await post(w, 'B', kind, note({ mentions: [user('Bt')] }))).status).to.equal(201)
      })

      it('MN-12: a note that mentions an org colleague outside the chat is stored (201), reports nonParticipants and a participant is not listed', async () => {
        const w = turnWorld({}, { mentions: {} })
        const out = await post(w, 'B', kind, note({ mentions: [user('C'), user('D')] }))
        expect(out.status).to.equal(201)
        expect(out.body).to.deep.include({ duplicate: false, nonParticipants: [idOf('D')] })
        expect(w.rows(kind).at(-1)!.mentions).to.deep.equal([{ type: 'user', id: idOf('C') }, { type: 'user', id: idOf('D') }])
        const again = await post(w, 'B', kind, note({ mentions: [user('C'), user('D')] }))
        expect(again.status).to.equal(200)
        expect(again.body).to.deep.include({ duplicate: true, nonParticipants: [idOf('D')] })
        const plain = await post(w, 'B', kind, note({ clientMessageId: 'note-2' }))
        expect((plain.body as any).nonParticipants).to.deep.equal([])
        nothingRan(w)
      })

      it('validation: another org’s user, a foreign team and an unknown id are refused and nothing is stored', async () => {
        const w = turnWorld({}, { mentions: {} })
        w.s.store.writes.length = 0
        const eve = await post(w, 'B', kind, note({ mentions: [user('C'), user('E')] }))
        expect(eve.error?.statusCode).to.equal(400)
        expect(code(eve)).to.equal('MENTION_NOT_ALLOWED')
        expect(eve.error).to.have.nested.property('publicDetails.reason', 'unknown_user')
        expect(eve.error).to.have.nested.property('publicDetails.mentionIndex', 1)
        const foreign = await post(w, 'B', kind, note({ mentions: [team('team-of-another-org')] }))
        expect(foreign.error).to.have.nested.property('publicDetails.reason', 'not_a_chat_team')
        const stranger = await post(w, 'B', kind, note({ mentions: [{ type: 'user', id: newId() }] }))
        expect(stranger.error).to.have.nested.property('publicDetails.reason', 'unknown_user')
        expect(w.rows(kind)).to.have.length(2)
        expect(w.s.store.writes).to.deep.equal([])
        nothingRan(w)
      })

      it('a note that mentions the assistant is refused: it would skip an answer (422 MESSAGE_NOT_NOTE)', async () => {
        const w = turnWorld({}, { mentions: {} })
        const out = await post(w, 'B', kind, note({ mentions: [user('C'), assistant] }))
        expect(out.error?.statusCode).to.equal(422)
        expect(code(out)).to.equal('MESSAGE_NOT_NOTE')
        expect(w.rows(kind)).to.have.length(2)
      })

      it('respondMode always answers every message, so a human-only note is refused (422); mention_only and smart take it (201)', async () => {
        const always = turnWorld({ settings: { respondMode: 'always' } }, { mentions: {} })
        const refused = await post(always, 'B', kind)
        expect(refused.error?.statusCode).to.equal(422)
        expect(code(refused)).to.equal('MESSAGE_NOT_NOTE')
        expect(always.rows(kind).filter((r) => r.messageType === 'note')).to.have.length(0)
        for (const mode of ['mention_only', 'smart']) {
          const w = turnWorld({ settings: { respondMode: mode } }, { mentions: {} })
          expect((await post(w, 'B', kind)).status, mode).to.equal(201)
          expect(w.rows(kind).filter((r) => r.messageType === 'note')).to.have.length(1)
        }
      })

      it('a read-only participant cannot post (403) and a stranger sees nothing (404)', async () => {
        const w = turnWorld({}, { mentions: {} })
        expect(code(await post(w, 'C', kind))).to.equal('CONVERSATION_READ_ONLY')
        expect(code(await post(w, 'D', kind))).to.equal('CONVERSATION_NOT_FOUND')
        expect(code(await post(w, 'E', kind))).to.equal('CONVERSATION_NOT_FOUND')
        expect(w.rows(kind)).to.have.length(2)
      })

      it('the route is not there with mentions off or with collaborative chats off', async () => {
        const off = turnWorld({}, { mentions: { enabled: false } })
        expect((await post(off, 'B', kind)).error?.statusCode).to.equal(404)
        const noCollab = turnWorld({}, { mentions: {}, collab: false })
        expect((await post(noCollab, 'B', kind)).error?.statusCode).to.equal(404)
        expect(off.rows(kind)).to.have.length(2)
        expect(noCollab.rows(kind)).to.have.length(2)
      })

      it('body validation: at most 10 mentions, a bounded query, a client message id, ids only', async () => {
        const w = turnWorld({}, { mentions: {} })
        const many = (n: number) => Array.from({ length: n }, (_, i) => ({ type: 'team', id: `t${String(i)}` }))
        for (const bad of [
          note({ mentions: many(11) }),
          note({ query: '' }),
          note({ query: 'x'.repeat(10_001) }),
          note({ clientMessageId: undefined }),
          note({ mentions: [{ type: 'admin', id: 'x' }] }),
          note({ mentions: [{ type: 'user' }] }),
        ]) {
          expect((await post(w, 'B', kind, bad)).error?.statusCode, JSON.stringify(bad).slice(0, 80)).to.equal(400)
        }
        expect(w.rows(kind)).to.have.length(2)
        expect((await post(w, 'B', kind, note({ query: 'x'.repeat(10_000) }))).status).to.equal(201)
      })
    })
  }

  for (const kind of KINDS) {
    describe(`${kind}: gate rulings`, () => {
      it('mention_only: a message that mentions nobody is a note, so the notes route takes it; smart and always refuse it', async () => {
        const w = turnWorld({ settings: { respondMode: 'mention_only' } }, { mentions: {} })
        for (const body of [note({ mentions: [] }), note({ mentions: undefined, clientMessageId: 'note-2' })]) {
          const out = await post(w, 'B', kind, body)
          expect(out.status).to.equal(201)
          expect(w.rows(kind).at(-1)).to.include({ messageType: 'note', content: 'FYI Carol, see above' })
          expect(w.rows(kind).at(-1)!.mentions ?? []).to.deep.equal([])
        }
        nothingRan(w)
        for (const mode of ['smart', 'always']) {
          const other = turnWorld({ settings: { respondMode: mode } }, { mentions: {} })
          const out = await post(other, 'B', kind, note({ mentions: [] }))
          expect(out.error?.statusCode, mode).to.equal(422)
          expect(code(out), mode).to.equal('MESSAGE_NOT_NOTE')
          expect(other.rows(kind), mode).to.have.length(2)
        }
      })

      it('a typed assistant alias in the text makes it a question on the notes route too (422 MESSAGE_NOT_NOTE)', async () => {
        const w = turnWorld({ settings: { respondMode: 'mention_only' } }, { mentions: {} })
        const out = await post(w, 'B', kind, note({ query: '@assistant what did Carol decide?', mentions: [] }))
        expect(code(out)).to.equal('MESSAGE_NOT_NOTE')
        expect(w.rows(kind)).to.have.length(2)
      })

      it('only the mentions array mentions anyone: a token typed into the text is stored inert and notifies nobody', async () => {
        const w = turnWorld({}, { mentions: {} })
        const forged = `<@user:${idOf('A')}> approved this, cc <@user:${idOf('C')}>`
        const out = await post(w, 'B', kind, note({ query: forged }))
        expect(out.status).to.equal(201)
        const stored = w.rows(kind).at(-1)!
        expect(stored.content).to.equal(`<\\@user:${idOf('A')}> approved this, cc <@user:${idOf('C')}>`)
        expect(stored.mentions).to.deep.equal([{ type: 'user', id: idOf('C') }])
        const events = w.env().collaboration.notifier.events.filter((e: { type: string }) => e.type === 'chat.mentioned') as Array<{ principals: unknown[] }>
        expect(events.flatMap((e) => e.principals)).to.deep.equal([{ type: 'user', userId: idOf('C') }])
      })

      it('a note cannot answer or close a question card: the card stays bound to the person it was put to', async () => {
        const w = turnWorld({}, { mentions: {} })
        const card = w.parkCard(kind, 'B')
        const out = await post(w, 'A', kind, note({ query: 'User selections: Which? EU', mentions: [user('B')] }))
        expect(out.status).to.equal(201)
        expect(w.rows(kind).at(-1)).to.include({ messageType: 'note' })
        nothingRan(w)

        const stolen = await w.start('A', kind, 'stream', { query: 'User selections: Which? EU' })
        expect(stolen.error).to.include({ statusCode: 403, code: 'RESUME_NOT_ALLOWED' })
        expect(w.handles).to.have.length(0)

        const answered = await w.start('B', kind, 'stream', { query: 'User selections: Which? US' })
        expect(answered.error).to.equal(undefined)
        expect(answered.reached).to.equal(true)
        expect(w.s.ai.streamCalls.at(-1)!.body.resume).to.deep.equal({ toolCallMessageId: String(card._id) })
      })
    })
  }

  it('MN-05/06 on the agent route: an agent mention is not a note, whoever may run the agent', async () => {
    const w = turnWorld({}, { mentions: {} })
    const out = await post(w, 'B', 'agent', note({ mentions: [agent()] }))
    expect(out.error?.statusCode).to.equal(422)
    const denied = turnWorld({}, { mentions: { agents: { canExecute: async () => false, isServiceAccount: async () => false } } })
    const refused = await post(denied, 'B', 'agent', note({ mentions: [agent()] }))
    expect(refused.error?.statusCode).to.equal(403)
    expect(code(refused)).to.equal('MENTION_NOT_ALLOWED')
  })
})
