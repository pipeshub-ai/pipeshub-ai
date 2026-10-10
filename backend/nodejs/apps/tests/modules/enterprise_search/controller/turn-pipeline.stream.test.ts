import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { ACTORS, ORG } from '../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'
import { MongoConversationMessageFeed } from '../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'
import { LeaseLostError } from '../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { saveCompleteConversation } from '../../../../src/modules/enterprise_search/utils/utils'

const KINDS: Kind[] = ['chat', 'agent']
const aiPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat\/stream$/ : /\/agent\/agent-1\/chat\/stream$/)
const payloadOf = (w: TurnWorld, index = 0): Record<string, any> => w.s.ai.streamCalls[index]!.body

describe('follow-up stream turns under the run lease (flag on)', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    describe(kind === 'chat' ? 'C3 addMessageStream' : 'A2 addMessageStreamToAgentConversation', () => {
      it('M-01/J-03: B continues A’s conversation; the rows carry B and the payload is B’s with A’s history', async () => {
        const w = turnWorld()
        const turn = await w.start('B', kind, 'stream', { clientMessageId: 'k1', filesShared: true, shareToolResults: false })
        expect(turn.reached).to.equal(true)
        expect(w.s.ai.streamCalls[0]!.url).to.match(aiPath(kind))
        expect(w.s.ai.streamCalls[0]!.headers.authorization).to.equal('Bearer token-B')
        await w.answerStream('B’s answer')
        await turn.res.ended

        const rows = w.rows(kind)
        const [, , question, answer] = rows
        expect(rows).to.have.length(4)
        expect(String(question!.authorUserId)).to.equal(String(ACTORS.B.userId))
        expect(question).to.include({ clientMessageId: 'k1', filesShared: true, shareToolResults: false })
        expect(String(answer!.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(String(answer!.inReplyTo)).to.equal(String(question!._id))
        expect(answer!.content).to.equal('B’s answer')
        expect(payloadOf(w).previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
        expect(w.session(kind).status).to.equal('Complete')
        expect(w.session(kind).activeRun ?? null).to.equal(null)
      })

      it('PH05-02/13: the Node run id replaces the body run id, and rides in the header and the first frame; every row carries it', async () => {
        const w = turnWorld()
        const turn = await w.start('B', kind, 'stream', { runId: 'client-chosen' })
        const runId = turn.lease!.runId
        expect(runId).to.not.equal('client-chosen')
        expect(payloadOf(w).runId).to.equal(runId)
        expect(turn.res.headers['x-run-id']).to.equal(runId)
        const first = turn.res.events()[0]!
        expect(first.data).to.deep.include({ name: 'conversation_created' })
        expect(first.data.value).to.deep.equal({ conversationId: w.s.ids[kind], runId })

        w.s.ai.send('CUSTOM', { name: 'ask_user_question', value: { toolData: { question: 'Which?' } } })
        await w.answerStream()
        await turn.res.ended
        const mine = w.rows(kind).slice(2)
        expect(mine.map((m) => m.messageType)).to.deep.equal(['user_query', 'tool_call', 'bot_response'])
        expect(mine.map((m) => m.runId)).to.deep.equal([runId, runId, runId])
      })

      it('PH05-04: an ask card is stored with its asker before the answer; PH05-03: rev moves once per write', async () => {
        const w = turnWorld()
        const before = w.session(kind).rev ?? 0
        const turn = await w.start('B', kind)
        w.s.ai.send('CUSTOM', { name: 'ask_user_question', value: { toolData: { question: 'Which?' } } })
        await w.answerStream()
        await turn.res.ended

        const [, , question, card, answer] = w.rows(kind)
        expect(card!.messageType).to.equal('tool_call')
        expect(card!.seq).to.be.lessThan(answer!.seq)
        expect(String(card!.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(String(card!.inReplyTo)).to.equal(String(question!._id))
        // acquire + user row + card + answer + release
        expect(w.session(kind).rev - before).to.equal(5)
      })

      for (const [label, finish] of [
        [
          'the stream ends normally',
          async (w: TurnWorld) => {
            await w.answerStream()
          },
        ],
        [
          'the AI service reports RUN_ERROR',
          async (w: TurnWorld) => {
            w.s.ai.send('RUN_ERROR', { runId: 'run-root', message: 'boom', code: 'llm_error' })
            w.s.ai.finish()
            await settle()
          },
        ],
        [
          'the upstream connection breaks',
          async (w: TurnWorld) => {
            w.s.ai.breakConnection(Object.assign(new TypeError('terminated'), { cause: { code: 'UND_ERR_SOCKET' } }))
            await settle()
          },
        ],
        [
          'the stream ends without an answer',
          async (w: TurnWorld) => {
            w.s.ai.finish()
            await settle()
          },
        ],
      ] as const) {
        it(`LS-03: the lease is released exactly once when ${label}`, async () => {
          const w = turnWorld()
          const turn = await w.start('B', kind)
          await finish(w)
          await turn.res.ended
          await settle()
          expect(w.session(kind).activeRun ?? null).to.equal(null)
          expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
          expect(turn.res.writesAfterEnd).to.equal(0)
        })
      }

      it('LS-03/J-04: a client close saves what was shown, stops the AI call, releases once, and lets the next send in', async () => {
        const w = turnWorld()
        const turn = await w.start('A', kind)
        w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Half of it' })
        await settle()
        const blocked = await w.start('B', kind)
        expect(blocked.error?.code).to.equal(COLLAB_ERROR_CODES.BUSY)

        turn.res.disconnect()
        await settle(10)

        expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
        expect(w.session(kind).status).to.equal('Stopped')
        const partial = w.rows(kind).at(-1)!
        expect(partial).to.include({ messageType: 'bot_response', status: 'stopped', content: 'Half of it' })
        expect(String(partial.requestedBy)).to.equal(String(ACTORS.A.userId))
        const retry = await w.start('B', kind)
        expect(retry.reached).to.equal(true)
      })

      it('LS-03: a failure after the lease is taken, before the stream opens, fails the turn and releases', async () => {
        const w = turnWorld()
        w.s.ai.refuseStream(503, { detail: 'down' })
        const turn = await w.start('B', kind)
        await turn.res.ended
        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.unavailable])
        expect(w.session(kind).status).to.equal('Failed')
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
      })

      it('LS-10/DB-02: when another run took the lease, the answer is dropped and the stream ends stopped', async () => {
        const w = turnWorld()
        const turn = await w.start('A', kind)
        const rowsBefore = w.rows(kind).length
        w.takeOver(kind, 'B')
        await w.answerStream('A’s late answer')
        await turn.res.ended

        expect(w.rows(kind)).to.have.length(rowsBefore)
        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(turn.res.eventsOf('RUN_FINISHED')).to.deep.equal([])
        expect(w.session(kind).activeRun.runId).to.equal('run-of-someone-else')
      })

      it('a failed heartbeat aborts the upstream and ends the stream stopped', async () => {
        const w = turnWorld({}, { heartbeatMs: 10 })
        const turn = await w.start('A', kind)
        w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Working' })
        w.takeOver(kind, 'B')
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(w.rows(kind).map((r) => r.messageType)).to.deep.equal(['user_query', 'bot_response', 'user_query'])
        expect(w.session(kind).activeRun.runId).to.equal('run-of-someone-else')
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('CL-01: a conversation deleted mid-run is not revived by the terminal write', async () => {
        const w = turnWorld()
        const turn = await w.start('B', kind)
        w.s.store.session(w.s.ids[kind])!.set('isDeleted', true)
        await w.answerStream()
        await turn.res.ended

        expect(w.session(kind).isDeleted).to.equal(true)
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        const next = await w.start('B', kind)
        expect(next.error?.statusCode).to.equal(404)
      })

      it('LC-05: a collaborator removed mid-run still completes the run and the next send is 404', async () => {
        const w = turnWorld()
        const turn = await w.start('B', kind)
        const doc = w.s.store.session(w.s.ids[kind])!
        doc.set('sharedWith', (doc.get('sharedWith') as any[]).filter((r) => String(r.userId) !== String(ACTORS.B.userId)))
        await w.answerStream('Still answered.')
        await turn.res.ended

        expect(w.rows(kind).at(-1)).to.include({ content: 'Still answered.' })
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        const next = await w.start('B', kind)
        expect(next.error?.statusCode).to.be.oneOf([403, 404])
      })

      it('DB-09: a duplicate that slips past the guard is a JSON 409, releases the lease and adds no row', async () => {
        const w = turnWorld()
        w.hooks.beforeAcquire = () =>
          w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'first try', authorUserId: ACTORS.B.userId, clientMessageId: 'k' })
        const turn = await w.start('B', kind, 'stream', { clientMessageId: 'k' })

        expect(turn.error?.statusCode).to.equal(409)
        expect(turn.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(turn.res.headersSent).to.equal(false)
        expect(w.rows(kind).filter((r) => r.clientMessageId === 'k')).to.have.length(1)
        expect(w.s.ai.streamCalls).to.have.length(0)
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect(w.session(kind).status).to.equal('Complete')
        expect((turn.lease!.release as sinon.SinonSpy).callCount).to.equal(1)
      })

      it('DB-09: the same clientMessageId from another author is a different message', async () => {
        const w = turnWorld()
        w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'A’s', authorUserId: ACTORS.A.userId, clientMessageId: 'k' })
        const turn = await w.start('B', kind, 'stream', { clientMessageId: 'k' })
        expect(turn.reached).to.equal(true)
        expect(turn.error).to.equal(undefined)
      })
    })
  }

  it('LS-02: two sends at once: exactly one runs, the other is a 409 BUSY with no row', async () => {
    const w = turnWorld()
    const [x, y] = await Promise.all([w.start('A'), w.start('B')])
    const winner = x.error ? y : x
    const loser = x.error ? x : y
    expect(loser.error?.code).to.equal(COLLAB_ERROR_CODES.BUSY)
    expect(loser.res.headersSent).to.equal(false)
    await w.answerStream()
    await winner.res.ended
    expect(w.rows()).to.have.length(4)
    expect(w.s.ai.streamCalls).to.have.length(1)
  })

  it('LS-08: history is the rows before the caller’s question, not whatever a later writer added', async () => {
    const real = new MongoConversationMessageFeed()
    let injectLaterRow = (): void => undefined
    const w = turnWorld({}, { deps: { feed: { historyBefore: (...args) => (injectLaterRow(), real.historyBefore(...args)) } } })
    injectLaterRow = () =>
      w.s.store.addMessage(w.s.store.session(w.s.ids.chat)!, { messageType: 'user_query', content: 'sneaked in', authorUserId: ACTORS.A.userId })

    const turn = await w.start('B')

    expect(w.rows().map((r) => r.content)).to.include('sneaked in')
    expect(payloadOf(w).previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
    await w.answerStream()
    await turn.res.ended
  })

  it('LS-09: a stale handle’s terminal write leaves the session untouched', async () => {
    const w = turnWorld()
    const turn = await w.start('B')
    w.takeOver('chat', 'A')
    const stored = w.s.store.session(w.s.ids.chat)!
    // A separate copy, as the handler holds: the stored row only changes through the conditional update.
    const copy = new ChatSession(stored.toObject())
    const run = { lease: turn.lease!, requestedBy: ACTORS.B.userId, inReplyTo: ACTORS.B.userId }
    const error = await saveCompleteConversation(copy, { answer: 'late', citations: [] } as never, String(ORG), null, { chatMode: 'deep' } as never, run).then(
      () => undefined,
      (e: unknown) => e,
    )
    expect(error).to.be.instanceOf(LeaseLostError)
    expect(w.session().status).to.equal('Inprogress')
    expect(w.session().modelInfo?.chatMode).to.not.equal('deep')
    expect(w.rows().map((r) => r.content)).to.not.include('late')
  })
})
