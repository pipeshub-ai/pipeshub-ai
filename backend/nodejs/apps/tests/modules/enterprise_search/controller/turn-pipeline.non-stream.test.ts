import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { MongoConversationMessageFeed } from '../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

const chatPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/)
const answer = (text: string): Record<string, unknown> => ({ answer: text, citations: [], confidence: 'High' })

describe('non-streaming follow-up turns under the run lease (flag on)', () => {
  afterEach(() => sinon.restore())

  for (const [kind, label] of [
    ['chat', 'C1 addMessage'],
    ['agent', 'A1 addMessageToAgentConversation'],
  ] as const) {
    describe(label, () => {
      it('M-04: while a stream runs, a non-streaming send is a 409 BUSY with no row and no AI call', async () => {
        const w = turnWorld()
        const streaming = await w.start('A', kind, 'stream')
        w.s.ai.reply(chatPath(kind), 200, answer('x'))
        const rowsBefore = w.rows(kind).length

        const blocked = await w.start('B', kind, 'plain')

        expect(blocked.error?.code).to.equal(COLLAB_ERROR_CODES.BUSY)
        expect(w.rows(kind)).to.have.length(rowsBefore)
        expect(w.s.ai.calls.filter((c) => chatPath(kind).test(c.url))).to.have.length(0)
        streaming.res.disconnect()
        await settle(10)
      })

      it('M-11: when idle it answers as the caller, names the run, and releases before replying', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 200, answer('B’s answer'))

        const turn = await w.start('B', kind, 'plain', { clientMessageId: 'k', runId: 'client-chosen' })

        expect(turn.error).to.equal(undefined)
        expect(turn.res.statusCode).to.equal(200)
        const sent = w.s.ai.calls.find((c) => chatPath(kind).test(c.url))!
        expect(sent.body.runId).to.equal(turn.lease!.runId)
        expect(sent.body.previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
        expect(turn.res.headers['x-run-id']).to.equal(turn.lease!.runId)
        const [, , question, reply] = w.rows(kind)
        expect(String(question!.authorUserId)).to.equal(String(ACTORS.B.userId))
        expect(String(reply!.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(String(reply!.inReplyTo)).to.equal(String(question!._id))
        expect(reply!.runId).to.equal(turn.lease!.runId)
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
      })

      it('M-11: a 5xx from the AI service fails the turn and still releases the lease', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 500, { detail: 'boom' })

        const turn = await w.start('B', kind, 'plain')

        expect(turn.error?.statusCode).to.equal(500)
        expect(w.session(kind).status).to.equal('Failed')
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
      })

      it('a lease taken over during the call drops the answer and is a 409 RUN_LOST', async () => {
        const real = new MongoConversationMessageFeed()
        let takeOver = (): void => undefined
        const w = turnWorld({}, { deps: { feed: { historyBefore: (...args) => (takeOver(), real.historyBefore(...args)) } } })
        takeOver = () => w.takeOver(kind, 'A')
        w.s.ai.reply(chatPath(kind), 200, answer('late'))

        const turn = await w.start('B', kind, 'plain')

        expect(turn.error?.code).to.equal(COLLAB_ERROR_CODES.RUN_LOST)
        expect(turn.error?.statusCode).to.equal(409)
        expect(w.rows(kind).map((r) => r.content)).to.not.include('late')
        expect(w.session(kind).activeRun.runId).to.equal('run-of-someone-else')
      })

      it('DB-09: a duplicate that slips past the guard is a 409 and releases the lease', async () => {
        const w = turnWorld()
        w.hooks.beforeAcquire = () =>
          w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'first', authorUserId: ACTORS.B.userId, clientMessageId: 'k' })
        const turn = await w.start('B', kind, 'plain', { clientMessageId: 'k' })
        expect(turn.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect(w.s.ai.calls).to.have.length(0)
      })
    })
  }
})
