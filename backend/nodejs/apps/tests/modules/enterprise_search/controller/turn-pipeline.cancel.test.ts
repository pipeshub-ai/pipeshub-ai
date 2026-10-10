import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { randomUUID } from 'crypto'
import jwt from 'jsonwebtoken'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../helpers/turn-world'
import { settle } from './chat-test-harness'

const KINDS: Array<[Kind, string]> = [
  ['chat', 'C12'],
  ['agent', 'A5'],
]
const CANCEL = /\/api\/v1\/chat\/cancel$/
const PARTICIPANT_CANCEL = /\/api\/v1\/chat\/cancel\/participant$/
const cancelCalls = (w: TurnWorld): Array<Record<string, any>> => w.s.ai.calls.filter((c) => CANCEL.test(c.url)).map((c) => c.body)

describe('cancel (C12, A5) stops the run that holds the conversation', () => {
  afterEach(() => sinon.restore())

  for (const [kind, route] of KINDS) {
    describe(route, () => {
      it('PH05-10: with A’s run live, B (write) cancels it by naming that run; Python is asked to stop exactly that run', async () => {
        const w = turnWorld()
        w.s.ai.reply(PARTICIPANT_CANCEL, 200, { cancelled: true })
        const running = await w.start('A', kind)
        const runId = running.lease!.runId

        const out = await w.invoke('B', route, { runId })

        expect(out.error).to.equal(undefined)
        expect(out.body).to.deep.equal({ cancelled: true })
        expect(w.s.ai.calls.filter((c) => PARTICIPANT_CANCEL.test(c.url)).map((c) => c.body)).to.deep.equal([
          { runId, conversationId: w.s.ids[kind] },
        ])
        running.res.disconnect()
        await settle(10)
      })

      it('PH05-10: a body run id that is not the live run is { cancelled: false } and Python is not called', async () => {
        const w = turnWorld()
        w.s.ai.reply(CANCEL, 200, { cancelled: true })
        const running = await w.start('A', kind)

        const out = await w.invoke('B', route, { runId: randomUUID() })

        expect(out.error).to.equal(undefined)
        expect(out.status).to.equal(200)
        expect(out.body).to.deep.equal({ cancelled: false })
        expect(cancelCalls(w)).to.deep.equal([])
        expect(w.session(kind).activeRun.runId).to.equal(running.lease!.runId)
        running.res.disconnect()
        await settle(10)
      })

      it('with no run in progress it is { cancelled: false } and Python is not called', async () => {
        const w = turnWorld()
        w.s.ai.reply(CANCEL, 200, { cancelled: true })

        const out = await w.invoke('A', route, { runId: randomUUID() })

        expect(out.body).to.deep.equal({ cancelled: false })
        expect(cancelCalls(w)).to.deep.equal([])
      })

      it('once the run has ended its id no longer reaches Python', async () => {
        const w = turnWorld()
        w.s.ai.reply(CANCEL, 200, { cancelled: true })
        const running = await w.start('A', kind)
        const runId = running.lease!.runId
        await w.answerStream('Done')
        await running.res.ended

        const out = await w.invoke('A', route, { runId })

        expect(out.body).to.deep.equal({ cancelled: false })
        expect(cancelCalls(w)).to.deep.equal([])
      })

      it('LS-07: B (write) cancelling A’s run goes to Python’s participant route with a token bound to that conversation and run', async () => {
        const w = turnWorld()
        w.s.ai.reply(PARTICIPANT_CANCEL, 200, { cancelled: true })
        const running = await w.start('A', kind)
        const runId = running.lease!.runId

        const out = await w.invoke('B', route, { runId })

        expect(out.body).to.deep.equal({ cancelled: true })
        expect(cancelCalls(w)).to.deep.equal([])
        const [call] = w.s.ai.calls.filter((c) => PARTICIPANT_CANCEL.test(c.url))
        expect(call.body).to.deep.equal({ runId, conversationId: w.s.ids[kind] })
        const token = call.headers.authorization.replace('Bearer ', '')
        const claims = jwt.verify(token, 's') as Record<string, any>
        expect(claims).to.include({ userId: String(ACTORS.B.userId), conversationId: w.s.ids[kind], runId })
        expect(claims.scopes).to.deep.equal([TokenScopes.CONVERSATION_CANCEL])
        expect(claims.exp - claims.iat).to.equal(60)
        running.res.disconnect()
        await settle(10)
      })

      it('LS-07: A cancelling their own run uses the user route with no participant call', async () => {
        const w = turnWorld()
        w.s.ai.reply(CANCEL, 200, { cancelled: true })
        const running = await w.start('A', kind)

        await w.invoke('A', route, { runId: running.lease!.runId })

        expect(cancelCalls(w)).to.have.length(1)
        expect(w.s.ai.calls.filter((c) => PARTICIPANT_CANCEL.test(c.url))).to.deep.equal([])
        running.res.disconnect()
        await settle(10)
      })

      it('LS-07: a reader (C) cannot cancel A’s run: denied, nothing reaches Python', async () => {
        const w = turnWorld()
        const running = await w.start('A', kind)

        const out = await w.invoke('C', route, { runId: running.lease!.runId })

        expect(out.error?.statusCode).to.equal(403)
        expect(w.s.ai.calls.filter((c) => /cancel/.test(c.url))).to.deep.equal([])
        running.res.disconnect()
        await settle(10)
      })
    })
  }

  describe('flag off', () => {
    for (const [kind, route] of KINDS) {
      it(`${route}: the body run id is forwarded as before, with no lookup of the conversation’s run`, async () => {
        const w = turnWorld({ projectId: undefined, projectVisibility: undefined, isShared: false, sharedWith: [] }, { collab: false })
        w.s.ai.reply(CANCEL, 200, { cancelled: true })
        const runId = randomUUID()

        const out = await w.invoke('A', route, { runId })

        expect(out.body).to.deep.equal({ cancelled: true })
        expect(cancelCalls(w)).to.deep.equal([{ runId, conversationId: w.s.ids[kind] }])
      })
    }
  })
})
