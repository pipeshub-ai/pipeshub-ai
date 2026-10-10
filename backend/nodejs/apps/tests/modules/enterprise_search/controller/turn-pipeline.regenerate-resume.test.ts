import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'
import { ACTORS, ActorName } from '../helpers/conversation-world'
import { Kind, Turn, TurnWorld, turnWorld } from '../helpers/turn-world'
import { oid, settle } from './chat-test-harness'

const KINDS: Kind[] = ['chat', 'agent']
const SOLO = { projectId: undefined, projectVisibility: undefined, isShared: false, sharedWith: [] }
const regenPath = (kind: Kind): RegExp => (kind === 'chat' ? /\/api\/v1\/chat\/stream$/ : /\/agent\/agent-1\/chat\/stream$/)
const released = (turn: Turn): number => (turn.lease!.release as sinon.SinonSpy).callCount
const payloadOf = (w: TurnWorld): Record<string, any> => w.s.ai.streamCalls[0]!.body

/** The seeded Q1/A1 plus a second turn; returns that turn's question and answer rows. */
function secondTurn(w: TurnWorld, kind: Kind, asker: ActorName): { question: Record<string, any>; answer: Record<string, any> } {
  const doc = w.s.store.session(w.s.ids[kind])!
  const question = w.s.store.addMessage(doc, { messageType: 'user_query', content: 'second question', authorUserId: ACTORS[asker].userId })
  const answer = w.s.store.addMessage(doc, { messageType: 'bot_response', content: 'second answer' })
  return { question: question.toObject() as Record<string, any>, answer: answer.toObject() as Record<string, any> }
}

describe('regenerate (C11, A4) under the run lease (flag on)', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    describe(kind === 'chat' ? 'C11 regenerateAnswers' : 'A4 regenerateAgentAnswers', () => {
      it('M-06/CL-19: only the asker may regenerate, the owner included; B’s regeneration is B’s, with the history before the question', async () => {
        const w = turnWorld({}, { questionAuthor: ACTORS.B.userId })
        const { question, answer } = secondTurn(w, kind, 'B')
        const rev = w.session(kind).rev ?? 0

        const denied = await w.startRegenerate('A', kind, {}, String(answer._id))
        expect(denied.error?.statusCode).to.equal(403)
        expect(denied.error?.code).to.equal(COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED)
        expect(denied.reached).to.equal(false)
        expect(denied.res.headersSent).to.equal(false)
        expect(w.s.ai.calls).to.deep.equal([])
        expect(w.handles).to.have.length(0)
        expect(w.s.store.writes).to.deep.equal([])

        const turn = await w.startRegenerate('B', kind, { runId: '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c' }, String(answer._id))
        expect(turn.reached).to.equal(true)
        const runId = turn.lease!.runId
        expect(w.s.ai.streamCalls[0]!.url).to.match(regenPath(kind))
        expect(w.s.ai.streamCalls[0]!.headers.authorization).to.equal('Bearer token-B')
        expect(payloadOf(w)).to.include({ query: 'second question', runId, conversationId: w.s.ids[kind] })
        expect(payloadOf(w).previousConversations.map((m: any) => m.content)).to.deep.equal(['question', 'answer'])
        expect(turn.res.headers['x-run-id']).to.equal(runId)
        expect(turn.res.events()[0]!.data.value).to.deep.include({ runId })

        await w.answerStream('Regenerated answer')
        await turn.res.ended

        const rows = w.rows(kind)
        expect(rows.map((r) => r.content)).to.deep.equal(['question', 'answer', 'second question', 'Regenerated answer'])
        const replaced = rows[3]!
        expect(String(replaced._id), 'replaced in place').to.equal(String(answer._id))
        expect(replaced.seq).to.equal(answer.seq)
        expect(String(replaced.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(String(replaced.inReplyTo)).to.equal(String(question._id))
        expect(replaced.runId).to.equal(runId)
        expect(w.session(kind).status).to.equal('Complete')
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        // acquire + replace + release
        expect(w.session(kind).rev - rev).to.equal(3)
        expect(released(turn)).to.equal(1)
        expect(turn.res.eventsOf('RUN_FINISHED')).to.have.length(1)
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('O-1: the owner who asked may regenerate and B, a writer who did not ask, may not', async () => {
        const w = turnWorld({}, { questionAuthor: ACTORS.A.userId })
        const { answer } = secondTurn(w, kind, 'A')

        const denied = await w.startRegenerate('B', kind, {}, String(answer._id))
        expect(denied.error?.code).to.equal(COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED)
        expect(w.handles).to.have.length(0)

        const turn = await w.startRegenerate('A', kind, {}, String(answer._id))
        expect(turn.reached).to.equal(true)
        await w.answerStream('A again')
        await turn.res.ended
        expect(String(w.rows(kind).at(-1)!.requestedBy)).to.equal(String(ACTORS.A.userId))
      })

      it('a question saved before collaboration belongs to the owner', async () => {
        const w = turnWorld()
        const denied = await w.startRegenerate('B', kind)
        expect(denied.error?.code).to.equal(COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED)
        const turn = await w.startRegenerate('A', kind)
        expect(turn.reached).to.equal(true)
        turn.res.disconnect()
        await settle(10)
      })

      it('the project scope is the asker’s: the guard proves B’s own project access and the payload uses that project', async () => {
        const assertAtLeast = sinon.stub().resolves({ _id: 'project-of-b' })
        const w = turnWorld({}, { questionAuthor: ACTORS.B.userId, assertAtLeast })
        const turn = await w.startRegenerate('B', kind)
        expect(turn.reached).to.equal(true)
        expect(assertAtLeast.firstCall.args[0].userId).to.equal(String(ACTORS.B.userId))
        turn.res.disconnect()
        await settle(10)
      })

      it('lease contention: while A’s turn runs, B’s regeneration is a JSON 409 BUSY, before any SSE header, with no AI call and no change', async () => {
        const w = turnWorld({}, { questionAuthor: ACTORS.B.userId })
        const running = await w.start('A', kind)
        const rowsBefore = w.rows(kind).length

        const blocked = await w.startRegenerate('B', kind)

        expect(blocked.error?.statusCode).to.equal(409)
        expect(blocked.error?.code).to.equal(COLLAB_ERROR_CODES.BUSY)
        expect(blocked.res.headersSent).to.equal(false)
        expect(w.s.ai.streamCalls).to.have.length(1)
        expect(w.rows(kind)).to.have.length(rowsBefore)
        expect(w.session(kind).activeRun.userId.toString()).to.equal(String(ACTORS.A.userId))
        running.res.disconnect()
        await settle(10)
      })

      for (const [label, finish, status, lastFrameFromUs] of [
        [
          'the stream ends with an answer',
          async (w: TurnWorld) => {
            await w.answerStream('Done')
          },
          'Complete',
          true,
        ],
        [
          'the AI service reports RUN_ERROR',
          async (w: TurnWorld) => {
            w.s.ai.send('RUN_ERROR', { runId: 'run-root', message: 'boom', code: 'llm_error' })
            w.s.ai.finish()
            await settle()
          },
          'Failed',
          false,
        ],
        [
          'the upstream connection breaks',
          async (w: TurnWorld) => {
            w.s.ai.breakConnection(Object.assign(new TypeError('terminated'), { cause: { code: 'UND_ERR_SOCKET' } }))
            await settle()
          },
          'Failed',
          true,
        ],
        [
          'the stream ends without an answer',
          async (w: TurnWorld) => {
            w.s.ai.finish()
            await settle()
          },
          'Failed',
          true,
        ],
      ] as const) {
        it(`LS-03: the lease is released exactly once, before the last frame, when ${label}`, async () => {
          const w = turnWorld()
          const turn = await w.startRegenerate('A', kind)
          const freeAtLastFrame: boolean[] = []
          const write = turn.res.write.bind(turn.res)
          turn.res.write = (chunk: string) => {
            if (/RUN_FINISHED|RUN_ERROR/.test(chunk)) freeAtLastFrame.push((w.session(kind).activeRun ?? null) === null)
            return write(chunk)
          }
          await finish(w)
          await turn.res.ended
          await settle()

          expect(w.session(kind).activeRun ?? null).to.equal(null)
          expect(w.session(kind).status).to.equal(status)
          expect(released(turn)).to.equal(1)
          expect(turn.res.writesAfterEnd).to.equal(0)
          // An upstream RUN_ERROR is relayed as it arrives, ahead of the release, as on the follow-up routes.
          if (lastFrameFromUs) {
            expect(freeAtLastFrame.length).to.be.greaterThan(0)
            expect(freeAtLastFrame.every(Boolean), 'the conversation is free when the browser sees the last frame').to.equal(true)
          }
        })
      }

      it('a RUN_ERROR replaces the answer with the failure, stamped with the asker and the run', async () => {
        const w = turnWorld()
        const turn = await w.startRegenerate('A', kind)
        w.s.ai.send('RUN_ERROR', { runId: 'run-root', message: 'The model is down.', code: 'llm_error' })
        w.s.ai.finish()
        await turn.res.ended

        const row = w.rows(kind)[1]!
        expect(row).to.include({ messageType: 'error', content: 'The model is down.', runId: turn.lease!.runId })
        expect(String(row.requestedBy)).to.equal(String(ACTORS.A.userId))
        expect(turn.res.eventsOf('RUN_ERROR')).to.have.length(1)
      })

      it('a client close saves what was shown in place, stops, and releases once', async () => {
        const w = turnWorld()
        const turn = await w.startRegenerate('A', kind)
        w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Half of it' })
        await settle()

        turn.res.disconnect()
        await settle(10)

        expect(released(turn)).to.equal(1)
        expect(w.session(kind).status).to.equal('Stopped')
        expect(w.rows(kind)[1]).to.include({ messageType: 'bot_response', status: 'stopped', content: 'Half of it', runId: turn.lease!.runId })
        expect(w.rows(kind)).to.have.length(2)
      })

      it('LS-10: when another run took the lease, the replacement is dropped and the stream ends stopped', async () => {
        const w = turnWorld()
        const turn = await w.startRegenerate('A', kind)
        w.takeOver(kind, 'B')
        await w.answerStream('A’s late answer')
        await turn.res.ended

        expect(w.rows(kind)[1]!.content).to.equal('answer')
        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(turn.res.eventsOf('RUN_FINISHED')).to.deep.equal([])
        expect(w.session(kind).activeRun.runId).to.equal('run-of-someone-else')
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('a failed heartbeat aborts the upstream and ends the stream stopped', async () => {
        const w = turnWorld({}, { heartbeatMs: 10 })
        const turn = await w.startRegenerate('A', kind)
        w.s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: 'Working' })
        w.takeOver(kind, 'B')
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['abort'])
        expect(w.rows(kind)[1]!.content).to.equal('answer')
        expect(w.session(kind).activeRun.runId).to.equal('run-of-someone-else')
        expect(turn.res.writesAfterEnd).to.equal(0)
      })

      it('a target that is no longer the last answer fails inside the stream and still frees the conversation', async () => {
        const w = turnWorld()
        secondTurn(w, kind, 'A')
        const turn = await w.startRegenerate('A', kind)
        await turn.res.ended

        expect(turn.res.eventsOf('RUN_ERROR')[0]?.data.message).to.equal('Can only regenerate the last message in the conversation')
        expect(w.s.ai.streamCalls).to.have.length(0)
        expect(released(turn)).to.equal(1)
        expect(w.session(kind).activeRun ?? null).to.equal(null)
        expect(w.session(kind).status).to.equal('Complete')
      })

      it('stale ask cards of the regenerated turn are dropped, the replacement carries its own card', async () => {
        const w = turnWorld()
        const card = w.parkCard(kind, 'A')
        const turn = await w.startRegenerate('A', kind)
        w.s.ai.send('CUSTOM', { name: 'ask_user_question', value: { toolData: { questions: [{ question: 'Which region?' }] } } })
        await w.answerStream('Which region?')
        await turn.res.ended

        const rows = w.rows(kind)
        expect(rows.map((r) => String(r._id))).to.not.include(String(card._id))
        expect(rows[1]!.tools?.[0]?.toolName).to.equal('ask_user_question')
        expect(released(turn)).to.equal(1)
      })
    })
  }
})

describe('regenerate with the flag off keeps today’s behaviour', () => {
  afterEach(() => sinon.restore())

  for (const kind of KINDS) {
    it(`${kind}: no lease, the body run id is forwarded, the history is the rows before the question, and only the inert author fields are new`, async () => {
      const w = turnWorld(SOLO, { collab: false })
      const earlier = formatPreviousConversations(w.rows(kind) as never)
      const { answer } = secondTurn(w, kind, 'A')

      const turn = await w.startRegenerate('A', kind, { runId: '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c' }, String(answer._id))
      expect(turn.reached).to.equal(true)
      expect(turn.lease).to.equal(undefined)
      expect(payloadOf(w)).to.deep.equal({
        query: 'second question',
        previousConversations: earlier,
        filters: {},
        attachments: [],
        modelKey: null,
        modelName: null,
        modelFriendlyName: null,
        reasoningEffort: null,
        chatMode: 'quick',
        conversationId: w.s.ids[kind],
        timezone: null,
        currentTime: null,
        runId: '3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c',
        protocol: 'agui',
      })
      expect(turn.res.headers).to.not.have.property('x-run-id')
      expect(turn.res.events()[0]!.data).to.deep.equal({ type: 'CUSTOM', name: 'conversation_created', value: { message: 'SSE connection established' } })

      await w.answerStream('Regenerated')
      await turn.res.ended

      const replaced = w.rows(kind)[3]!
      expect(replaced).to.include({ content: 'Regenerated', messageType: 'bot_response' })
      expect(replaced).to.not.have.property('runId')
      expect(String(replaced.requestedBy)).to.equal(String(ACTORS.A.userId))
      expect(w.session(kind).activeRun ?? null).to.equal(null)
      expect(w.session(kind).status).to.equal('Complete')
      expect(turn.res.eventsOf('RUN_FINISHED')).to.have.length(1)
    })
  }

  it('anyone who could regenerate before still can: no asker check, no lease, no 409', async () => {
    const w = turnWorld(SOLO, { collab: false })
    const [x, y] = await Promise.all([w.startRegenerate('A'), w.startRegenerate('A')])
    expect(x.error).to.equal(undefined)
    expect(y.error).to.equal(undefined)
    expect(w.handles).to.have.length(0)
    x.res.disconnect()
    y.res.disconnect()
    await settle(10)
  })
})

describe('resume binding (DF-8) on the send routes', () => {
  afterEach(() => sinon.restore())

  const SELECTIONS = 'User selections: Which region? EU'
  const nothingHappened = (w: TurnWorld): void => {
    expect(w.s.ai.calls, 'AI calls').to.deep.equal([])
    expect(w.handles, 'leases').to.have.length(0)
    expect(w.s.store.writes, 'writes').to.deep.equal([])
  }
  const denied = (out: { error?: Error & { statusCode?: number; code?: string }; res: { headersSent: boolean } }): void => {
    expect(out.error?.statusCode).to.equal(403)
    expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED)
    expect(out.res.headersSent).to.equal(false)
  }

  for (const [kind, route] of [
    ['chat', 'C3'],
    ['agent', 'A2'],
  ] as const) {
    describe(`${route} through the router`, () => {
      for (const [label, body] of [
        ['a resume object', (card: Record<string, any>) => ({ resume: { toolCallMessageId: String(card._id) } })],
        ['the User selections: text', () => ({ query: SELECTIONS })],
        // The AI backend matches the prefix after Python's `lstrip()`.
        ['the User selections: text after leading whitespace', () => ({ query: ` \n\x1c\x85${SELECTIONS}` })],
      ] as const) {
        it(`SEC-07/J-09: B parked the question; A answering with ${label} is 403 RESUME_NOT_ALLOWED, takes no lease, calls no AI, writes nothing`, async () => {
          const w = turnWorld()
          const card = w.parkCard(kind, 'B')
          w.s.store.writes.length = 0

          const out = await w.invoke('A', route, body(card))

          denied(out)
          nothingHappened(w)
          expect(w.session(kind).activeRun ?? null).to.equal(null)
          expect(w.rows(kind).at(-1)!._id).to.deep.equal(card._id)
        })

        it(`J-09: B answering with ${label} runs: B’s row, B’s lease, and the payload names the card`, async () => {
          const w = turnWorld()
          const card = w.parkCard(kind, 'B')

          const pending = w.invoke('B', route, body(card))
          await settle()
          expect(w.handles).to.have.length(1)
          expect(w.s.ai.streamCalls).to.have.length(1)
          expect(w.s.ai.streamCalls[0]!.body.resume).to.deep.equal({ toolCallMessageId: String(card._id) })
          await w.answerStream('Thanks B')
          const out = await pending

          expect(out.error).to.equal(undefined)
          const question = w.rows(kind).find((r) => r.messageType === 'user_query' && r.authorUserId && String(r.authorUserId) === String(ACTORS.B.userId))!
          expect(question).to.not.equal(undefined)
          expect(w.session(kind).activeRun ?? null).to.equal(null)
          expect((w.handles[0]!.release as sinon.SinonSpy).callCount).to.equal(1)
        })
      }

      it('a legacy card without requestedBy belongs to the author of the question before it: the owner here', async () => {
        const w = turnWorld()
        const card = w.parkCard(kind)
        w.s.store.writes.length = 0

        denied(await w.invoke('B', route, { resume: { toolCallMessageId: String(card._id) } }))
        nothingHappened(w)

        const pending = w.invoke('A', route, { resume: { toolCallMessageId: String(card._id) } })
        await settle()
        expect(w.s.ai.streamCalls[0]!.body.resume).to.deep.equal({ toolCallMessageId: String(card._id) })
        await w.answerStream('Thanks A')
        expect((await pending).error).to.equal(undefined)
      })

      it('a legacy card follows the author of the question that preceded it, not the owner', async () => {
        const w = turnWorld({}, { questionAuthor: ACTORS.B.userId })
        const card = w.parkCard(kind)
        w.s.store.writes.length = 0

        denied(await w.invoke('A', route, { query: SELECTIONS }))
        nothingHappened(w)

        const pending = w.invoke('B', route, { query: SELECTIONS })
        await settle()
        expect(w.s.ai.streamCalls[0]!.body.resume).to.deep.equal({ toolCallMessageId: String(card._id) })
        await w.answerStream('Thanks B')
        expect((await pending).error).to.equal(undefined)
      })

      it('a card a regeneration embedded in its answer is answered by the asker only', async () => {
        const w = turnWorld()
        const card = w.parkCard(kind, 'B', { messageType: 'bot_response', content: '' })
        w.s.store.writes.length = 0

        denied(await w.invoke('A', route, { query: SELECTIONS }))
        nothingHappened(w)

        const pending = w.invoke('B', route, { query: SELECTIONS })
        await settle()
        expect(w.s.ai.streamCalls[0]!.body.resume).to.deep.equal({ toolCallMessageId: String(card._id) })
        await w.answerStream('Thanks B')
        expect((await pending).error).to.equal(undefined)
      })

      it('PH05-09: a card that is not the newest unanswered one, one already answered, and one of another session are all refused', async () => {
        const w = turnWorld()
        const older = w.parkCard(kind, 'B')
        const newer = w.parkCard(kind, 'B')
        const other = w.parkCard(kind === 'chat' ? 'agent' : 'chat', 'B')
        w.s.store.writes.length = 0

        denied(await w.invoke('B', route, { resume: { toolCallMessageId: String(older._id) } }))
        denied(await w.invoke('B', route, { resume: { toolCallMessageId: String(other._id) } }))
        denied(await w.invoke('B', route, { resume: { toolCallMessageId: String(oid()) } }))
        nothingHappened(w)

        w.s.store.addMessage(w.s.store.session(w.s.ids[kind])!, { messageType: 'user_query', content: 'moved on', authorUserId: ACTORS.B.userId })
        denied(await w.invoke('B', route, { resume: { toolCallMessageId: String(newer._id) } }))
        expect(w.handles).to.have.length(0)
      })

      it('a row that is not an ask card cannot be resumed', async () => {
        const w = turnWorld()
        denied(await w.invoke('A', route, { resume: { toolCallMessageId: w.s.messageIds[kind] } }))
        nothingHappened(w)
      })

      it('in a shared chat the User selections: text with no pending card of its own is 403', async () => {
        const w = turnWorld()
        denied(await w.invoke('A', route, { query: SELECTIONS }))
        nothingHappened(w)
      })

      it('D11: resume with the user directory down is 503 OWNER_STATUS_UNAVAILABLE for a non-owner, before the binding, any lease or AI call', async () => {
        const w = turnWorld({}, { ownerDirectoryDown: true })
        const card = w.parkCard(kind, 'B')
        w.s.store.writes.length = 0

        const out = await w.invoke('B', route, { resume: { toolCallMessageId: String(card._id) } })

        expect(out.error?.statusCode).to.equal(503)
        expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.OWNER_STATUS_UNAVAILABLE)
        expect(out.res.headersSent).to.equal(false)
        nothingHappened(w)
      })
    })
  }

  describe('what counts as collaborative', () => {
    for (const [label, over] of [
      ['a share row', { projectId: undefined, projectVisibility: undefined }],
      ['project visibility', { sharedWith: [], isShared: false }],
    ] as const) {
      it(`${label} alone makes the User selections: text subject to the binding`, async () => {
        const w = turnWorld(over)
        denied(await w.invoke('A', 'C3', { query: SELECTIONS }))
        nothingHappened(w)
      })
    }

    it('PH05-08: in a solo chat the owner’s User selections: text is a plain message, as before', async () => {
      const w = turnWorld(SOLO)
      const pending = w.invoke('A', 'C3', { query: SELECTIONS })
      await settle()
      expect(w.s.ai.streamCalls).to.have.length(1)
      expect(w.s.ai.streamCalls[0]!.body).to.not.have.property('resume')
      expect(w.s.ai.streamCalls[0]!.body.query).to.equal(SELECTIONS)
      await w.answerStream('Noted')
      expect((await pending).error).to.equal(undefined)
    })

    it('a solo chat’s explicit resume is still bound: with no card of the caller’s it is 403', async () => {
      const w = turnWorld(SOLO)
      denied(await w.invoke('A', 'C3', { resume: { toolCallMessageId: String(oid()) } }))
      nothingHappened(w)
    })
  })

  describe('flag off', () => {
    it('the User selections: text of a shared-looking chat and a resume object are not bound, and nothing new reaches the AI backend', async () => {
      const w = turnWorld({}, { collab: false })
      w.parkCard('chat', 'B')
      const pending = w.invoke('A', 'C3', { query: SELECTIONS, resume: { toolCallMessageId: String(oid()) } })
      await settle()
      expect(w.s.ai.streamCalls).to.have.length(1)
      expect(w.s.ai.streamCalls[0]!.body).to.not.have.property('resume')
      expect(w.handles).to.have.length(0)
      await w.answerStream('Fine')
      expect((await pending).error).to.equal(undefined)
    })
  })

  describe('a first send has no card to answer', () => {
    for (const [label, kind, mode] of [
      ['streamChat', 'chat', 'stream'],
      ['createConversation', 'chat', 'plain'],
      ['streamAgentConversation', 'agent', 'stream'],
      ['createAgentConversation', 'agent', 'plain'],
    ] as const) {
      it(`${label}: a resume is 403 RESUME_NOT_ALLOWED before anything is created or sent`, async () => {
        const w = turnWorld()
        const turn = await w.startCreate('A', kind, mode, { resume: { toolCallMessageId: String(new Types.ObjectId()) } })

        denied(turn)
        expect(w.created()).to.have.length(0)
        expect(w.handles).to.have.length(0)
        expect(w.s.ai.calls).to.deep.equal([])
        expect(turn.res.body).to.equal('')
      })
    }

    it('with the flag off the field is ignored: the conversation is created and Python is not told', async () => {
      const w = turnWorld(SOLO, { collab: false })
      const turn = await w.startCreate('A', 'chat', 'stream', { resume: { toolCallMessageId: String(new Types.ObjectId()) } })
      expect(turn.error).to.equal(undefined)
      expect(w.created()).to.have.length(1)
      expect(w.s.ai.streamCalls[0]!.body).to.not.have.property('resume')
      turn.res.disconnect()
      await settle(10)
    })
  })
})
