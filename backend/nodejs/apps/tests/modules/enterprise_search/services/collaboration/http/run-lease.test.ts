import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ForbiddenError, NotFoundError } from '../../../../../../src/libs/errors/http.errors'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { COLLAB_ERROR_CODES } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { ConversationGuards, RunLeaseOptions } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import {
  claimLease,
  conversationContextOf,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { LeaseLostError, LeaseHandle } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { MongoRunLeaseManager } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'
import { IAgentReadinessPort } from '../../../../../../src/modules/enterprise_search/services/collaboration/readiness/agent-readiness.port'
import { FakeSSEResponse } from '../../../controller/chat-test-harness'
import { realGuards } from '../../../helpers/guarded-chat'
import { ACTORS, ActorName, ORG, PROJECT, Seed, seed, teamsOf } from '../../../helpers/conversation-world'
import { AGENT_KEY } from '../../../helpers/conversation-routes'

type Kind = 'chat' | 'agent'
type Op = NonNullable<RunLeaseOptions['op']>

interface Outcome {
  error: (Error & { statusCode?: number; code?: string; publicDetails?: Record<string, unknown> }) | undefined
  reached: boolean
  res: FakeSSEResponse
  req: any
}

describe('ConversationGuards.runLease', () => {
  let clock: FixedClock
  let leases: MongoRunLeaseManager
  let handles: LeaseHandle[]
  let readiness: { check: sinon.SinonStub; invalidate: sinon.SinonStub }
  let beforeAcquire: (() => void) | undefined
  let s: Seed

  const leaseManager = (): any => ({
    forNewSession: leases.forNewSession.bind(leases),
    acquire: async (...args: Parameters<MongoRunLeaseManager['acquire']>) => {
      beforeAcquire?.()
      const handle = await leases.acquire(...args)
      sinon.spy(handle, 'startHeartbeat')
      handles.push(handle)
      return handle
    },
  })

  const build = (options: { collab?: boolean; assertAtLeast?: sinon.SinonStub; teamsUnresolved?: boolean; withLeases?: boolean } = {}): ConversationGuards =>
    realGuards({
      collab: options.collab ?? true,
      teamIds: teamsOf,
      leases: options.withLeases === false ? undefined : leaseManager(),
      readiness: options.withLeases === false ? undefined : (readiness as IAgentReadinessPort),
      assertAtLeast: options.assertAtLeast,
      teamsUnresolved: options.teamsUnresolved,
    })

  const request = (who: ActorName, kind: Kind, body: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => {
    const a = ACTORS[who]
    return {
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com`, ...extra },
      params: { conversationId: s.ids[kind], messageId: s.messageIds[kind], ...(kind === 'agent' && { agentKey: AGENT_KEY }) },
      body,
      headers: {},
      context: { requestId: 'req' },
    } as any
  }

  const chain = (guards: ConversationGuards, kind: Kind, req: any, options: RunLeaseOptions = {}, op: Op = options.op ?? 'send', res = new FakeSSEResponse()): Promise<Outcome> =>
    new Promise((resolve) => {
      const done = (error?: unknown, reached = false) => resolve({ error: error as Outcome['error'], reached, res, req })
      guards.authorize(op, kind)(req, res as any, (e?: unknown) => {
        if (e !== undefined) return done(e)
        void guards.runLease(kind, options)(req, res as any, (e2?: unknown) => (e2 !== undefined ? done(e2) : done(undefined, true)))
      })
    })

  const send = (who: ActorName, kind: Kind = 'chat', body: Record<string, unknown> = {}, guards = build(), options: RunLeaseOptions = {}) =>
    chain(guards, kind, request(who, kind, body), options)

  const stored = (kind: Kind = 'chat') =>
    s.store.session(s.ids[kind])!.toObject() as { activeRun?: { userId: unknown; runId: string } | null; status: string; rev?: number }

  const denied = (out: Outcome, status: number, code: string): void => {
    expect(out.error?.statusCode).to.equal(status)
    expect(out.error?.code).to.equal(code)
    expect(out.reached).to.equal(false)
    expect(out.res.headersSent, 'no SSE header').to.equal(false)
    expect(out.error ? out.req && conversationContextOf(out.req).lease : undefined, 'no lease on the context').to.equal(undefined)
  }

  /** One more turn row by `who`; the world's own two rows sit at seq 1-2. */
  const addTurn = (who: ActorName | 'legacy', messageType = 'user_query', extra: Record<string, unknown> = {}, kind: Kind = 'chat') => {
    const author = who === 'legacy' ? {} : messageType === 'user_query' ? { authorUserId: ACTORS[who].userId } : { requestedBy: ACTORS[who].userId }
    return s.store.addMessage(s.store.session(s.ids[kind])!, { messageType, content: 'x', ...author, ...extra })
  }

  beforeEach(() => {
    s = seed()
    clock = new FixedClock(Date.parse('2026-10-02T10:00:00Z'))
    leases = new MongoRunLeaseManager({ clock, instanceId: 'pod', logger: { warn: sinon.stub() }, users: { displayNames: async () => new Map([[String(ACTORS.A.userId), 'Alice']]), findByIds: async () => [] } })
    handles = []
    beforeAcquire = undefined
    readiness = { check: sinon.stub().resolves({ status: 'ready' }), invalidate: sinon.stub() }
  })
  afterEach(() => sinon.restore())

  describe('flag off', () => {
    it('is a pass-through: no checks, no lease, no writes', async () => {
      const guards = build({ collab: false })
      const out = await send('A', 'agent', { baseSeq: -1, clientMessageId: 'k' }, guards)
      expect(out.reached).to.equal(true)
      expect(conversationContextOf(out.req).lease).to.equal(undefined)
      expect(readiness.check.called).to.equal(false)
      expect(stored('agent').activeRun ?? null).to.equal(null)
      expect(s.store.writes).to.deep.equal([])
    })

    it('does not need the lease dependencies', async () => {
      const out = await send('A', 'chat', {}, build({ collab: false, withLeases: false }))
      expect(out.reached).to.equal(true)
    })
  })

  describe('flag on', () => {
    it('acquires for the caller, stores the handle on the context, and does not start the heartbeat', async () => {
      const out = await send('B')
      expect(out.reached).to.equal(true)
      const { lease, project } = conversationContextOf(out.req)
      expect(lease).to.equal(handles[0])
      expect(project).to.not.equal(undefined)
      expect(String(stored().activeRun!.userId)).to.equal(String(ACTORS.B.userId))
      expect(stored().activeRun!.runId).to.equal(lease!.runId)
      expect(stored().status).to.equal('Inprogress')
      expect((handles[0].startHeartbeat as sinon.SinonSpy).called).to.equal(false)
    })

    it('fails closed when the lease dependencies are not configured', async () => {
      const out = await send('B', 'chat', {}, build({ withLeases: false }))
      expect(out.error?.statusCode).to.equal(500)
      expect(out.reached).to.equal(false)
    })
  })

  describe('step 1: baseSeq', () => {
    it('CL-21: newer rows by someone else are a 409 with their count, and no lease is taken', async () => {
      addTurn('A') // seq 3
      addTurn('A', 'bot_response') // seq 4
      s.store.writes.length = 0
      const out = await send('B', 'chat', { baseSeq: 2 })
      denied(out, 409, COLLAB_ERROR_CODES.CHANGED)
      expect(out.error!.publicDetails).to.deep.equal({ newerCount: 2 })
      expect(stored().activeRun ?? null).to.equal(null)
      expect(s.store.writes).to.deep.equal([])
    })

    it('a single newer row by someone else is a conflict (nextSeq is the highest seq handed out)', async () => {
      addTurn('A') // seq 3
      const out = await send('B', 'chat', { baseSeq: 2 })
      denied(out, 409, COLLAB_ERROR_CODES.CHANGED)
      expect(out.error!.publicDetails).to.deep.equal({ newerCount: 1 })
    })

    it('counts only the other authors rows', async () => {
      addTurn('B')
      addTurn('B', 'bot_response')
      addTurn('A')
      const out = await send('B', 'chat', { baseSeq: 2 })
      expect(out.error!.publicDetails).to.deep.equal({ newerCount: 1 })
    })

    it('passes when only the callers own rows are newer', async () => {
      addTurn('B')
      addTurn('B', 'bot_response')
      expect((await send('B', 'chat', { baseSeq: 2 })).reached).to.equal(true)
    })

    it('passes when nothing is newer than baseSeq', async () => {
      expect((await send('B', 'chat', { baseSeq: 2 })).reached).to.equal(true)
    })

    it('passes the owner at baseSeq -1 when every row is theirs', async () => {
      expect((await send('A', 'chat', { baseSeq: -1 })).reached).to.equal(true)
    })

    it('reads legacy rows as the owners: a conflict for B, none for the owner', async () => {
      addTurn('legacy')
      addTurn('legacy', 'bot_response')
      const forB = await send('B', 'chat', { baseSeq: 2 })
      expect(forB.error!.publicDetails).to.deep.equal({ newerCount: 2 })
      handles.length = 0
      const forA = await send('A', 'chat', { baseSeq: 2 })
      expect(forA.reached).to.equal(true)
    })

    it('is not checked when the body has no baseSeq', async () => {
      addTurn('A')
      expect((await send('B')).reached).to.equal(true)
    })
  })

  describe('step 2: duplicate clientMessageId', () => {
    it('M-05: a repeat by the same author is a 409 with the message id and answered=false', async () => {
      const original = addTurn('B', 'user_query', { clientMessageId: 'k' })
      const out = await send('B', 'chat', { clientMessageId: 'k' })
      denied(out, 409, COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
      expect(out.error!.publicDetails).to.deep.equal({ messageId: String(original._id), answered: false })
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('answered=true once a bot_response replies to it', async () => {
      const original = addTurn('B', 'user_query', { clientMessageId: 'k' })
      addTurn('A', 'bot_response', { inReplyTo: original._id })
      const out = await send('B', 'chat', { clientMessageId: 'k' })
      expect(out.error!.publicDetails).to.deep.equal({ messageId: String(original._id), answered: true })
    })

    it('DB-09: the key is scoped by author', async () => {
      addTurn('A', 'user_query', { clientMessageId: 'k' })
      expect((await send('B', 'chat', { clientMessageId: 'k' })).reached).to.equal(true)
    })

    it('another conversations key is not a duplicate', async () => {
      addTurn('B', 'user_query', { clientMessageId: 'k' }, 'agent')
      expect((await send('B', 'chat', { clientMessageId: 'k' })).reached).to.equal(true)
    })
  })

  describe('step 3: readiness', () => {
    it('CL-08: a blocked agent is a 412 with the toolsets, before any lease', async () => {
      readiness.check.resolves({ status: 'blocked', toolsets: ['gmail'] })
      const out = await send('B', 'agent')
      denied(out, 412, COLLAB_ERROR_CODES.CONNECTOR_SETUP_REQUIRED)
      expect(out.error!.publicDetails).to.deep.equal({ toolsets: ['gmail'] })
      expect(readiness.check.firstCall.args).to.deep.equal([{ orgId: String(ORG), userId: String(ACTORS.B.userId) }, AGENT_KEY])
      expect(stored('agent').activeRun ?? null).to.equal(null)
    })

    it('CL-09: unknown is skipped', async () => {
      readiness.check.resolves({ status: 'unknown' })
      expect((await send('B', 'agent')).reached).to.equal(true)
    })

    it('is not asked when the send narrows its tools: the AI backend checks only the selected toolsets', async () => {
      readiness.check.resolves({ status: 'blocked', toolsets: ['gmail'] })
      expect((await send('B', 'agent', { tools: ['slack.post'] })).reached).to.equal(true)
      expect(readiness.check.called).to.equal(false)
    })

    it('is not asked for a plain chat', async () => {
      await send('B', 'chat')
      expect(readiness.check.called).to.equal(false)
    })

    it('is skipped for service accounts', async () => {
      readiness.check.resolves({ status: 'blocked', toolsets: ['gmail'] })
      const out = await chain(build(), 'agent', request('B', 'agent', {}, { isServiceAccount: true }))
      expect(out.reached).to.equal(true)
      expect(readiness.check.called).to.equal(false)
    })
  })

  describe('step 4: project', () => {
    it('R-08: no access to the project is a 403 PROJECT_ACCESS_REQUIRED and no lease', async () => {
      const assertAtLeast = sinon.stub().rejects(new ForbiddenError('no'))
      const out = await send('B', 'chat', {}, build({ assertAtLeast }))
      denied(out, 403, COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED)
      expect(stored().activeRun ?? null).to.equal(null)
      expect(assertAtLeast.firstCall.args[1]).to.equal(String(PROJECT))
      expect(assertAtLeast.firstCall.args[2]).to.equal('viewer')
      expect(assertAtLeast.firstCall.args[0].userId).to.equal(String(ACTORS.B.userId))
    })

    it('a project that is gone (NotFound) is the same 403', async () => {
      const out = await send('B', 'chat', {}, build({ assertAtLeast: sinon.stub().rejects(new NotFoundError('gone')) }))
      denied(out, 403, COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED)
    })

    it('any other failure propagates instead of reading as a denial', async () => {
      const boom = new Error('db down')
      const out = await send('B', 'chat', {}, build({ assertAtLeast: sinon.stub().rejects(boom) }))
      expect(out.error).to.equal(boom)
    })

    it('a conversation without a project skips the check', async () => {
      s = seed({ projectId: undefined, projectVisibility: 'private' })
      const assertAtLeast = sinon.stub()
      const out = await send('B', 'chat', {}, build({ assertAtLeast }))
      expect(out.reached).to.equal(true)
      expect(assertAtLeast.called).to.equal(false)
      expect(conversationContextOf(out.req).project).to.equal(undefined)
    })

    it('with teams unresolved, a denial is retried once with the resolved teams', async () => {
      const assertAtLeast = sinon.stub()
      assertAtLeast.onFirstCall().rejects(new ForbiddenError('no'))
      assertAtLeast.onSecondCall().resolves({ _id: 'project' })
      const out = await send('B', 'chat', {}, build({ assertAtLeast }))
      expect(out.reached).to.equal(true)
      expect(assertAtLeast.firstCall.args[0].teamIds).to.equal('unresolved')
      expect(assertAtLeast.secondCall.args[0].teamIds).to.deep.equal([])
    })

    it('with teams that cannot be resolved, a denial is a 503, not a 403', async () => {
      const out = await send('B', 'chat', {}, build({ assertAtLeast: sinon.stub().rejects(new ForbiddenError('no')), teamsUnresolved: true }))
      expect(out.error?.statusCode).to.equal(503)
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE)
    })

    it('PI-03: a project editor under an editor ceiling acquires through the project path', async () => {
      const out = await send('PE')
      expect(out.reached).to.equal(true)
      expect(String(stored().activeRun!.userId)).to.equal(String(ACTORS.PE.userId))
    })
  })

  describe('step 5: acquire', () => {
    it('LS-01: a live lease is a 409 BUSY with the holder, and nothing changes', async () => {
      await send('A')
      const before = stored()
      const out = await send('B')
      denied(out, 409, COLLAB_ERROR_CODES.BUSY)
      expect(out.error!.publicDetails).to.deep.include({ activeRun: { userId: String(ACTORS.A.userId), displayName: 'Alice', startedAt: new Date(clock.now()).toISOString() } })
      expect(stored().activeRun).to.deep.equal(before.activeRun)
      expect(stored().rev).to.equal(before.rev)
    })

    it('LS-02: concurrent sends: exactly one acquires', async () => {
      const guards = build()
      const [x, y] = await Promise.all([chain(guards, 'chat', request('A', 'chat')), chain(guards, 'chat', request('B', 'chat'))])
      expect([x, y].filter((o) => o.reached)).to.have.length(1)
      expect([x, y].filter((o) => o.error?.code === COLLAB_ERROR_CODES.BUSY)).to.have.length(1)
    })

    it('LS-04: an expired lease is taken over', async () => {
      await send('A')
      clock.advance(120_001)
      const out = await send('B')
      expect(out.reached).to.equal(true)
      expect(String(stored().activeRun!.userId)).to.equal(String(ACTORS.B.userId))
    })

    it('a write to read downgrade between the guard and the acquire is a 403 READ_ONLY', async () => {
      beforeAcquire = () => {
        const session = s.store.session(s.ids.chat)!
        session.set('sharedWith', [{ userId: ACTORS.B.userId, accessLevel: 'read' }])
      }
      const out = await send('B')
      denied(out, 403, COLLAB_ERROR_CODES.READ_ONLY)
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('SEC-13: access revoked between the guard and the acquire stays a 404', async () => {
      beforeAcquire = () => {
        const session = s.store.session(s.ids.chat)!
        session.set('sharedWith', [])
        session.set('projectVisibility', 'private')
      }
      const out = await send('B')
      denied(out, 404, COLLAB_ERROR_CODES.NOT_FOUND)
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('a conversation deleted between the guard and the acquire stays a 404', async () => {
      beforeAcquire = () => {
        s.store.session(s.ids.chat)!.set('isDeleted', true)
      }
      denied(await send('B'), 404, COLLAB_ERROR_CODES.NOT_FOUND)
    })
  })

  describe('order: the first failing step wins', () => {
    const everythingWrong = async (skip: string[] = []) => {
      addTurn('A', 'user_query', { clientMessageId: 'k' }, 'agent') // seq 3 by A
      s.store.session(s.ids.agent)!.set('activeRun', { runId: 'r', userId: ACTORS.A.userId, instanceId: 'p', startedAt: new Date(clock.now()), leaseExpiresAt: new Date(clock.now() + 60_000) })
      addTurn('B', 'user_query', { clientMessageId: 'k' }, 'agent') // B's dup, seq 4
      readiness.check.resolves({ status: 'blocked', toolsets: ['gmail'] })
      const assertAtLeast = sinon.stub().rejects(new ForbiddenError('no'))
      return { assertAtLeast, skip }
    }

    it('baseSeq before duplicate', async () => {
      const { assertAtLeast } = await everythingWrong()
      const out = await send('B', 'agent', { baseSeq: 2, clientMessageId: 'k' }, build({ assertAtLeast }))
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.CHANGED)
    })

    it('duplicate before readiness', async () => {
      const { assertAtLeast } = await everythingWrong()
      const out = await send('B', 'agent', { clientMessageId: 'k' }, build({ assertAtLeast }))
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.DUPLICATE_MESSAGE)
      expect(readiness.check.called).to.equal(false)
    })

    it('readiness before project', async () => {
      const { assertAtLeast } = await everythingWrong()
      const out = await send('B', 'agent', {}, build({ assertAtLeast }))
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.CONNECTOR_SETUP_REQUIRED)
      expect(assertAtLeast.called).to.equal(false)
    })

    it('project before the lease', async () => {
      const { assertAtLeast } = await everythingWrong()
      readiness.check.resolves({ status: 'ready' })
      const out = await send('B', 'agent', {}, build({ assertAtLeast }))
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED)
    })

    it('the lease last: BUSY once everything else passes', async () => {
      await everythingWrong()
      readiness.check.resolves({ status: 'ready' })
      const out = await send('B', 'agent', {}, build())
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.BUSY)
    })

    it('regenerate skips baseSeq and duplicate but still checks readiness', async () => {
      addTurn('A')
      addTurn('B', 'user_query', { clientMessageId: 'k' })
      const body = { baseSeq: 2, clientMessageId: 'k' }
      const ok = await chain(build(), 'chat', request('B', 'chat', body), { op: 'regenerate' }, 'send')
      expect(ok.reached).to.equal(true)
      readiness.check.resolves({ status: 'blocked', toolsets: ['gmail'] })
      const blocked = await chain(build(), 'agent', request('B', 'agent', body), { op: 'regenerate' }, 'send')
      expect(blocked.error?.code).to.equal(COLLAB_ERROR_CODES.CONNECTOR_SETUP_REQUIRED)
    })

    it('a caller the guard denies never reaches the preconditions', async () => {
      const out = await send('C')
      expect(out.error?.code).to.equal(COLLAB_ERROR_CODES.READ_ONLY)
      expect(handles).to.have.length(0)
    })
  })

  describe('lease ownership between the middleware and the handler', () => {
    it('an unclaimed lease is released when the response closes (an error after the middleware)', async () => {
      const out = await send('B')
      expect(stored().activeRun).to.not.equal(null)
      out.res.status(500).json({ error: 'later middleware failed' })
      await Promise.resolve()
      await new Promise((resolve) => setImmediate(resolve))
      expect(stored().activeRun ?? null).to.equal(null)
      expect(stored().status).to.equal('Complete')
      expect((handles[0].startHeartbeat as sinon.SinonSpy).called).to.equal(false)
    })

    it('the unclaimed release puts back a Failed status left by the turn before', async () => {
      s.store.session(s.ids.chat)!.set({ status: 'Failed', failReason: 'earlier turn failed' })
      const out = await send('B')
      expect(stored().status).to.equal('Inprogress')
      out.res.status(500).json({ error: 'later middleware failed' })
      await new Promise((resolve) => setImmediate(resolve))
      expect(stored()).to.include({ activeRun: null, status: 'Failed', failReason: 'earlier turn failed' })
    })

    it('a client disconnect before the handler runs releases it too', async () => {
      const out = await send('B')
      out.res.disconnect()
      await new Promise((resolve) => setImmediate(resolve))
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('a claimed lease is left to the handler when the response closes', async () => {
      const out = await send('B')
      expect(claimLease(out.req)).to.equal(handles[0])
      out.res.end()
      await new Promise((resolve) => setImmediate(resolve))
      expect(stored().activeRun).to.not.equal(null)
      await handles[0].release('Complete')
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('claiming after the unclaimed release throws LeaseLostError', async () => {
      const out = await send('B')
      out.res.end()
      await new Promise((resolve) => setImmediate(resolve))
      expect(() => claimLease(out.req)).to.throw(LeaseLostError)
    })

    it('a request that already ended while acquiring releases at once and does not continue', async () => {
      const res = new FakeSSEResponse()
      const req = request('B', 'chat')
      const guards = build()
      let continued = false
      await new Promise<void>((resolve) => {
        guards.authorize('send', 'chat')(req, res as any, () => {
          res.end()
          void guards.runLease('chat')(req, res as any, () => {
            continued = true
          })
          setTimeout(resolve, 20)
        })
      })
      expect(continued).to.equal(false)
      expect(stored().activeRun ?? null).to.equal(null)
    })

    it('claimLease without a lease on the context is an internal error', async () => {
      const out = await send('A', 'chat', {}, build({ collab: false }))
      expect(() => claimLease(out.req)).to.throw('run lease not acquired')
    })
  })

  describe('no SSE header on any denial', () => {
    it('across every denial above', async () => {
      addTurn('A')
      const cases: Array<() => Promise<Outcome>> = [
        () => send('B', 'chat', { baseSeq: 1 }),
        () => send('B', 'chat', {}, build({ assertAtLeast: sinon.stub().rejects(new ForbiddenError('no')) })),
        () => send('D'),
      ]
      for (const run of cases) {
        const out = await run()
        expect(out.error, String(cases.indexOf(run))).to.not.equal(undefined)
        expect(out.res.headersSent).to.equal(false)
        expect(out.res.body).to.equal('')
      }
    })
  })
})
