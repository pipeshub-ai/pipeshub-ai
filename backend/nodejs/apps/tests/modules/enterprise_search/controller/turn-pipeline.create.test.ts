import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ForbiddenError } from '../../../../src/libs/errors/http.errors'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { ACTORS, PROJECT } from '../helpers/conversation-world'
import { Kind, Turn, TurnWorld, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

const KINDS: Kind[] = ['chat', 'agent']
const aiPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat\/stream$/ : /\/agent\/agent-1\/chat\/stream$/)
const chatPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat$/ : /\/agent\/agent-1\/chat$/)
const answer = (text: string): Record<string, unknown> => ({ answer: text, citations: [], confidence: 'High' })
const released = (w: TurnWorld, index = 0): number => (w.handles[index]!.release as sinon.SinonSpy).callCount
const NOTHING_SENT = (turn: Turn): void => {
  expect(turn.res.headersSent, 'no SSE header').to.equal(false)
  expect(turn.res.body, 'no SSE bytes').to.equal('')
}

describe('first send under the run lease and creationKey (flag on)', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    describe(kind === 'chat' ? 'streamChat' : 'streamAgentConversation', () => {
      it('PH05-07: the session is born holding the lease, with creationKey and the author’s first row; released on end', async () => {
        const w = turnWorld()
        const turn = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k1', runId: 'client-chosen', filesShared: true })
        expect(turn.error).to.equal(undefined)
        const [fresh] = w.created()
        expect(w.created()).to.have.length(1)
        expect(fresh).to.include({ creationKey: 'k1', status: 'Inprogress', sessionType: kind })
        expect(String(fresh!.initiator)).to.equal(String(ACTORS.A.userId))
        expect(String(fresh!.activeRun.userId)).to.equal(String(ACTORS.A.userId))
        const runId = fresh!.activeRun.runId
        expect(w.handles).to.have.length(1)
        expect(w.handles[0]!.runId).to.equal(runId)

        const [question] = w.rowsOf(fresh!._id)
        expect(question).to.include({ messageType: 'user_query', content: 'First question', clientMessageId: 'k1', filesShared: true, runId })
        expect(String(question!.authorUserId)).to.equal(String(ACTORS.A.userId))

        await w.answerStream('The answer.')
        await turn.res.ended

        const [, reply] = w.rowsOf(fresh!._id)
        expect(reply).to.include({ messageType: 'bot_response', runId, content: 'The answer.' })
        expect(String(reply!.requestedBy)).to.equal(String(ACTORS.A.userId))
        expect(String(reply!.inReplyTo)).to.equal(String(question!._id))
        const after = w.created()[0]!
        expect(after.status).to.equal('Complete')
        expect(after.activeRun ?? null).to.equal(null)
        expect(released(w)).to.equal(1)
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('PH05-02: the Node run id replaces the body run id; header, first frame and Python payload agree', async () => {
        const w = turnWorld()
        const turn = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k1', runId: '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c' })
        const [fresh] = w.created()
        const runId = fresh!.activeRun.runId as string

        expect(runId).to.not.equal('3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c')
        expect(w.s.ai.streamCalls[0]!.url).to.match(aiPath(kind))
        expect(w.s.ai.streamCalls[0]!.body).to.include({ runId, conversationId: String(fresh!._id), query: 'First question' })
        expect(turn.res.statusCode).to.equal(200)
        expect(turn.res.headers['x-run-id']).to.equal(runId)
        const order = w.s.store.writes.filter((write) => write === 'writeHead' || write === 'chatSession.save')
        expect(order, 'the session exists before the first byte').to.deep.equal(['chatSession.save', 'writeHead'])
        const first = turn.res.events()[0]!
        expect(first.event).to.equal('CUSTOM')
        expect(first.data).to.deep.equal({
          type: 'CUSTOM',
          name: 'conversation_created',
          value: { conversationId: String(fresh!._id), title: 'First question', runId },
        })
        turn.res.disconnect()
        await settle(10)
      })

      it('PH05-06: a repeated send is a 409 with the first conversation’s id, before any SSE byte, and creates nothing', async () => {
        const w = turnWorld()
        const first = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k1' })
        const [fresh] = w.created()
        const [question] = w.rowsOf(fresh!._id)

        const retry = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k1' })
        expect(retry.error?.statusCode).to.equal(409)
        expect(retry.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(retry.error?.publicDetails).to.deep.equal({ conversationId: String(fresh!._id), messageId: String(question!._id), answered: false })
        NOTHING_SENT(retry)
        expect(w.created()).to.have.length(1)
        expect(w.s.ai.streamCalls).to.have.length(1)
        expect(w.handles).to.have.length(1)

        await w.answerStream()
        await first.res.ended
        const afterAnswer = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k1' })
        expect(afterAnswer.error?.publicDetails).to.include({ conversationId: String(fresh!._id), answered: true })
        NOTHING_SENT(afterAnswer)
        expect(w.created()).to.have.length(1)
      })

      it('DB-09/PH05-06: two sends of one key at once create one session; the other is a 409 naming it, with no SSE byte and no lease', async () => {
        const w = turnWorld()
        const [x, y] = await Promise.all([
          w.startCreate('A', kind, 'stream', { clientMessageId: 'race' }),
          w.startCreate('A', kind, 'stream', { clientMessageId: 'race' }),
        ])
        const [winner, loser] = x.error ? [y, x] : [x, y]

        expect(winner.error).to.equal(undefined)
        expect(loser.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(loser.error?.publicDetails?.conversationId).to.equal(String(w.created()[0]!._id))
        NOTHING_SENT(loser)
        expect(w.created()).to.have.length(1)
        expect(w.s.store.writes.filter((write) => write === 'chatSession.save'), 'both reached the unique index').to.have.length(2)
        expect(w.s.ai.streamCalls).to.have.length(1)
        expect(w.handles, 'the loser never held a lease').to.have.length(1)
        winner.res.disconnect()
        await settle(10)
        expect(released(w)).to.equal(1)
      })

      it('F-17: the key is scoped by who starts the conversation; without a key every send creates one', async () => {
        const w = turnWorld()
        const a = await w.startCreate('A', kind, 'stream', { clientMessageId: 'shared-key' })
        const b = await w.startCreate('B', kind, 'stream', { clientMessageId: 'shared-key' })
        const c = await w.startCreate('A', kind, 'stream')
        const d = await w.startCreate('A', kind, 'stream')

        expect([a, b, c, d].map((t) => t.error)).to.deep.equal([undefined, undefined, undefined, undefined])
        expect(w.created()).to.have.length(4)
        expect(w.created().map((s) => s.creationKey)).to.deep.equal(['shared-key', 'shared-key', undefined, undefined])
        expect(w.created().map((s) => String(s.activeRun.userId))).to.deep.equal([ACTORS.A, ACTORS.B, ACTORS.A, ACTORS.A].map((x) => String(x.userId)))
        for (const t of [a, b, c, d]) t.res.disconnect()
        await settle(10)
      })

      for (const [label, finish] of [
        ['the stream ends normally', async (w: TurnWorld) => w.answerStream()],
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
          const turn = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })
          await finish(w)
          await turn.res.ended
          await settle()
          expect(w.created()[0]!.activeRun ?? null).to.equal(null)
          expect(released(w)).to.equal(1)
          expect(turn.res.writesAfterEnd).to.equal(0)
        })
      }

      it('LS-03: a client close keeps what was shown as Stopped, releases once, and the retry of the key is a 409 for that conversation', async () => {
        const w = turnWorld()
        const turn = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })
        w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Half of it' })
        await settle()
        turn.res.disconnect()
        await settle(10)

        const [fresh] = w.created()
        expect(released(w)).to.equal(1)
        expect(fresh!.status).to.equal('Stopped')
        expect(fresh!.activeRun ?? null).to.equal(null)
        expect(w.rowsOf(fresh!._id).at(-1)).to.include({ messageType: 'bot_response', status: 'stopped', content: 'Half of it' })
        const retry = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })
        expect(retry.error?.publicDetails?.conversationId).to.equal(String(fresh!._id))
      })

      it('LS-03: when the AI service refuses to start, the turn fails and the lease is released', async () => {
        const w = turnWorld()
        w.s.ai.refuseStream(503, { detail: 'down' })
        const turn = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.unavailable])
        const [fresh] = w.created()
        expect(fresh!.status).to.equal('Failed')
        expect(fresh!.activeRun ?? null).to.equal(null)
        expect(released(w)).to.equal(1)
      })

      it('LS-10: when another run took the lease, the answer is dropped, the stream ends stopped, and the other run keeps the lease', async () => {
        const w = turnWorld()
        const turn = await w.startCreate('A', kind, 'stream')
        const [fresh] = w.created()
        w.s.store.session(fresh!._id)!.set('activeRun', {
          runId: 'run-of-someone-else',
          userId: ACTORS.B.userId,
          startedAt: new Date(w.clock.now()),
          leaseExpiresAt: new Date(w.clock.now() + 120_000),
        })
        await w.answerStream('late')
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(turn.res.eventsOf('RUN_FINISHED')).to.deep.equal([])
        expect(w.rowsOf(fresh!._id).map((r) => r.messageType)).to.deep.equal(['user_query'])
        expect(w.created()[0]!.activeRun.runId).to.equal('run-of-someone-else')
        expect(released(w)).to.equal(1)
      })

      it('a failed heartbeat aborts the upstream and ends the stream stopped', async () => {
        const w = turnWorld({}, { heartbeatMs: 10 })
        const turn = await w.startCreate('A', kind, 'stream')
        const [fresh] = w.created()
        w.s.store.session(fresh!._id)!.set('activeRun', null)
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(released(w)).to.equal(1)
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('a write that fails before the stream opens is a JSON error, leaves nothing behind, and the key can be retried', async () => {
        const w = turnWorld()
        ;(ChatSessionMessage.insertMany as unknown as sinon.SinonStub).onFirstCall().rejects(new Error('disk full'))
        const failed = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })

        expect(failed.error?.message).to.equal('disk full')
        NOTHING_SENT(failed)
        expect(w.created(), 'the half-created session is removed').to.have.length(0)
        expect(w.handles).to.have.length(0)

        const retry = await w.startCreate('A', kind, 'stream', { clientMessageId: 'k' })
        expect(retry.error).to.equal(undefined)
        expect(w.created()).to.have.length(1)
        retry.res.disconnect()
        await settle(10)
      })

      it('PI-02: the project comes from the caller’s own access; a denial is a JSON 403 before anything is created', async () => {
        const w = turnWorld()
        const access = sinon.stub(ProjectService, 'assertAccess').rejects(new ForbiddenError('no access'))
        const denied = await w.startCreate('B', kind, 'stream', { clientMessageId: 'k', projectId: String(PROJECT) })
        expect(denied.error?.statusCode).to.equal(403)
        expect(access.firstCall.args.slice(1, 4)).to.deep.equal([String(ACTORS.B.userId), String(PROJECT), 'viewer'])
        NOTHING_SENT(denied)
        expect(w.created()).to.have.length(0)
        expect(w.handles).to.have.length(0)

        access.resolves({ project: { _id: PROJECT, name: 'P' } } as never)
        const ok = await w.startCreate('B', kind, 'stream', { clientMessageId: 'k', projectId: String(PROJECT) })
        expect(ok.error).to.equal(undefined)
        expect(String(w.created()[0]!.projectId)).to.equal(String(PROJECT))
        expect(ok.res.events()[0]!.data.value).to.include({ projectId: String(PROJECT) })
        ok.res.disconnect()
        await settle(10)
      })
    })
  }

  for (const kind of KINDS) {
    describe(kind === 'chat' ? 'createConversation' : 'createAgentConversation', () => {
      it('PH05-07: answers 201 as the caller, names the run in the header, and releases before replying', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 200, answer('Plain answer'))
        const turn = await w.startCreate('A', kind, 'plain', { clientMessageId: 'k1', runId: '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c' })

        expect(turn.error).to.equal(undefined)
        expect(turn.res.statusCode).to.equal(201)
        const [fresh] = w.created()
        const runId = w.handles[0]!.runId
        expect(runId).to.not.equal('3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c')
        expect(turn.res.headers['x-run-id']).to.equal(runId)
        expect(turn.res.headers['x-conversation-id']).to.equal(String(fresh!._id))
        expect(w.s.ai.calls.find((c) => chatPath(kind).test(c.url))!.body.runId).to.equal(runId)
        expect(fresh).to.include({ creationKey: 'k1', status: 'Complete' })
        expect(fresh!.activeRun ?? null).to.equal(null)
        const [question, reply] = w.rowsOf(fresh!._id)
        expect(question).to.include({ clientMessageId: 'k1', runId })
        expect(String(question!.authorUserId)).to.equal(String(ACTORS.A.userId))
        expect(reply).to.include({ content: 'Plain answer', runId })
        expect(String(reply!.requestedBy)).to.equal(String(ACTORS.A.userId))
        expect(released(w)).to.equal(1)
      })

      it('PH05-06: a repeat is a 409 with the conversation id and makes no AI call', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 200, answer('Plain answer'))
        await w.startCreate('A', kind, 'plain', { clientMessageId: 'k1' })
        const [fresh] = w.created()

        const retry = await w.startCreate('A', kind, 'plain', { clientMessageId: 'k1' })

        expect(retry.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
        expect(retry.error?.publicDetails).to.include({ conversationId: String(fresh!._id), answered: true })
        expect(w.created()).to.have.length(1)
        expect(w.s.ai.calls.filter((c) => chatPath(kind).test(c.url))).to.have.length(1)
        expect(w.handles).to.have.length(1)
      })

      it('DB-09: two sends of one key at once create one session and call the AI service once', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 200, answer('Plain answer'))
        const [x, y] = await Promise.all([w.startCreate('A', kind, 'plain', { clientMessageId: 'race' }), w.startCreate('A', kind, 'plain', { clientMessageId: 'race' })])

        expect([x, y].filter((t) => t.error === undefined)).to.have.length(1)
        const loser = x.error ? x : y
        expect(loser.error?.publicDetails?.conversationId).to.equal(String(w.created()[0]!._id))
        expect(w.created()).to.have.length(1)
        expect(w.s.ai.calls.filter((c) => chatPath(kind).test(c.url))).to.have.length(1)
        expect(w.handles).to.have.length(1)
        expect(released(w)).to.equal(1)
      })

      it('a 5xx from the AI service fails the turn and still releases the lease', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 500, { detail: 'boom' })
        const turn = await w.startCreate('A', kind, 'plain', { clientMessageId: 'k1' })

        expect(turn.error?.statusCode).to.equal(500)
        const [fresh] = w.created()
        expect(fresh!.status).to.equal('Failed')
        expect(fresh!.activeRun ?? null).to.equal(null)
        expect(released(w)).to.equal(1)
      })

      it('a lease taken over during the call drops the answer and is a 409 RUN_LOST', async () => {
        const w = turnWorld()
        w.s.ai.reply(chatPath(kind), 200, answer('late'))
        w.s.ai.onRequest = () =>
          w.s.store.session(w.created()[0]!._id)!.set('activeRun', {
            runId: 'other',
            userId: ACTORS.B.userId,
            startedAt: new Date(),
            leaseExpiresAt: new Date(w.clock.now() + 120_000),
          })
        const turn = await w.startCreate('A', kind, 'plain')
        const [fresh] = w.created()

        expect(turn.error?.code).to.equal(COLLAB_ERROR_CODES.RUN_LOST)
        expect(w.rowsOf(fresh!._id).map((r) => r.content)).to.not.include('late')
      })
    })
  }
})
