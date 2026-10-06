import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { InMemoryChatStore, oid } from '../../../controller/chat-test-harness'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { writeFilter } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters'
import { ConversationBusyError, ConversationNotFoundError } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { Caller } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { LEASE_TTL_MS } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease-filters'
import { MongoRunLeaseManager } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'

const T0 = Date.parse('2026-10-02T10:00:00Z')

describe('MongoRunLeaseManager', () => {
  const orgId = oid()
  const ownerId = oid()
  const guestId = oid()
  let store: InMemoryChatStore
  let clock: FixedClock
  let manager: MongoRunLeaseManager
  let sessionId: string

  const callerOf = (userId: unknown): Caller => ({ userId: String(userId), orgId: String(orgId), teamIds: [] })
  const filterFor = (caller: Caller, id = sessionId) =>
    writeFilter(caller, { kind: 'chat', conversationId: id }, { collab: true })
  const acquireAs = (userId: unknown, id = sessionId) => {
    const caller = callerOf(userId)
    return manager.acquire(id, caller, filterFor(caller, id))
  }
  const stored = () => store.session(sessionId)!.toObject() as {
    activeRun: { runId: string; userId: unknown; leaseExpiresAt: Date; startedAt: Date } | null
    status: string
    rev: number
    isDeleted?: boolean
  }

  beforeEach(() => {
    store = new InMemoryChatStore()
    store.install()
    clock = new FixedClock(T0)
    manager = new MongoRunLeaseManager({
      clock,
      instanceId: 'pod-1',
      users: { displayNames: async () => new Map([[String(ownerId), 'Alice']]), findByIds: async () => [] },
      logger: { warn: sinon.stub() },
    })
    const session = store.addSession({
      orgId,
      userId: ownerId,
      initiator: ownerId,
      isShared: true,
      sharedWith: [{ principalType: 'user', userId: guestId, accessLevel: 'write' }],
    })
    sessionId = String(session._id)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('acquire', () => {
    it('takes a free lease: writes activeRun, sets Inprogress, clears failReason, bumps rev', async () => {
      store.session(sessionId)!.set('failReason', 'old')
      const lease = await acquireAs(ownerId)
      const doc = stored()
      expect(doc.activeRun).to.include({ runId: lease.runId, instanceId: 'pod-1' })
      expect(String(doc.activeRun!.userId)).to.equal(String(ownerId))
      expect(doc.activeRun!.startedAt.getTime()).to.equal(T0)
      expect(doc.activeRun!.leaseExpiresAt.getTime()).to.equal(T0 + LEASE_TTL_MS)
      expect(doc.status).to.equal('Inprogress')
      expect(doc.rev).to.equal(1)
      expect((doc as Record<string, unknown>).failReason).to.equal(undefined)
      expect(lease.sessionId).to.equal(sessionId)
    })

    it('LS-01: throws ConversationBusyError with the holder while the lease is live', async () => {
      await acquireAs(ownerId)
      const before = stored()
      let error: unknown
      await acquireAs(guestId).catch((e: unknown) => {
        error = e
      })
      expect(error).to.be.instanceOf(ConversationBusyError)
      const busy = error as ConversationBusyError
      expect(busy.statusCode).to.equal(409)
      expect(busy.publicDetails).to.deep.equal({
        activeRun: { userId: String(ownerId), displayName: 'Alice', startedAt: new Date(T0).toISOString() },
      })
      expect(stored()).to.deep.equal(before)
    })

    it('still reports busy when the holder name cannot be resolved', async () => {
      const failing = new MongoRunLeaseManager({
        clock,
        users: { displayNames: async () => Promise.reject(new Error('down')), findByIds: async () => [] },
        logger: { warn: sinon.stub() },
      })
      const owner = callerOf(ownerId)
      await failing.acquire(sessionId, owner, filterFor(owner))
      const guest = callerOf(guestId)
      const error = await failing.acquire(sessionId, guest, filterFor(guest)).catch((e: unknown) => e)
      expect((error as ConversationBusyError).publicDetails).to.have.nested.property('activeRun.displayName', null)
    })

    it('LS-02: of two concurrent acquires exactly one wins', async () => {
      const results = await Promise.allSettled([acquireAs(ownerId), acquireAs(guestId)])
      expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1)
      const rejected = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected')
      expect(rejected).to.have.length(1)
      expect(rejected[0]!.reason).to.be.instanceOf(ConversationBusyError)
      expect(stored().rev).to.equal(1)
    })

    it('LS-04: takes over a lease that expired', async () => {
      const first = await acquireAs(ownerId)
      clock.advance(LEASE_TTL_MS + 1000)
      const second = await acquireAs(guestId)
      expect(second.runId).to.not.equal(first.runId)
      expect(stored().activeRun!.runId).to.equal(second.runId)
      expect(String(stored().activeRun!.userId)).to.equal(String(guestId))
    })

    it('SEC-13: throws not-found and writes nothing when the caller lost access', async () => {
      const caller = callerOf(guestId)
      const filter = filterFor(caller)
      store.session(sessionId)!.set('sharedWith', [])
      const error = await manager.acquire(sessionId, caller, filter).catch((e: unknown) => e)
      expect(error).to.be.instanceOf(ConversationNotFoundError)
      expect(stored().activeRun ?? null).to.equal(null)
      expect(stored().rev).to.equal(0)
    })

    it('does not reveal a live lease to a caller without access', async () => {
      await acquireAs(ownerId)
      const caller = callerOf(guestId)
      const filter = filterFor(caller)
      store.session(sessionId)!.set('sharedWith', [])
      const error = await manager.acquire(sessionId, caller, filter).catch((e: unknown) => e)
      expect(error).to.be.instanceOf(ConversationNotFoundError)
    })

    it('throws not-found for a deleted or unknown conversation', async () => {
      const caller = callerOf(ownerId)
      store.session(sessionId)!.set('isDeleted', true)
      expect(await manager.acquire(sessionId, caller, filterFor(caller)).catch((e: unknown) => e)).to.be.instanceOf(
        ConversationNotFoundError,
      )
      const other = String(oid())
      expect(await manager.acquire(other, caller, filterFor(caller, other)).catch((e: unknown) => e)).to.be.instanceOf(
        ConversationNotFoundError,
      )
    })

    it('does not cross orgs', async () => {
      const caller = { ...callerOf(ownerId), orgId: String(oid()) }
      const error = await manager.acquire(sessionId, caller, filterFor(caller)).catch((e: unknown) => e)
      expect(error).to.be.instanceOf(ConversationNotFoundError)
    })
  })

  describe('forNewSession', () => {
    it('returns an activeRun to embed and a handle bound to the new session', async () => {
      const { activeRun, bind } = manager.forNewSession(String(ownerId), String(orgId))
      expect(activeRun.leaseExpiresAt.getTime()).to.equal(T0 + LEASE_TTL_MS)
      expect(String(activeRun.userId)).to.equal(String(ownerId))
      store.session(sessionId)!.set('activeRun', activeRun)
      const lease = bind(sessionId)
      expect(lease.runId).to.equal(activeRun.runId)
      expect(await lease.renew()).to.equal(true)
      await lease.release('Complete')
      expect(stored().activeRun).to.equal(null)
    })
  })

  describe('LeaseHandle', () => {
    it('renew moves only leaseExpiresAt', async () => {
      const lease = await acquireAs(ownerId)
      const before = stored()
      clock.advance(30_000)
      expect(await lease.renew()).to.equal(true)
      const after = stored()
      expect(after.activeRun!.leaseExpiresAt.getTime()).to.equal(T0 + 30_000 + LEASE_TTL_MS)
      expect({ ...after, activeRun: { ...after.activeRun!, leaseExpiresAt: 0 } }).to.deep.equal({
        ...before,
        activeRun: { ...before.activeRun!, leaseExpiresAt: 0 },
      })
    })

    it('renew returns false once another run took the lease', async () => {
      const first = await acquireAs(ownerId)
      clock.advance(LEASE_TTL_MS + 1)
      const second = await acquireAs(guestId)
      expect(await first.renew()).to.equal(false)
      expect(await second.renew()).to.equal(true)
    })

    it('renew returns false after release', async () => {
      const lease = await acquireAs(ownerId)
      await lease.release('Complete')
      expect(await lease.renew()).to.equal(false)
    })

    it('release clears the lease, sets the status and bumps rev', async () => {
      const lease = await acquireAs(ownerId)
      await lease.release('Failed')
      expect(stored()).to.include({ activeRun: null, status: 'Failed', rev: 2 })
    })

    it('release is idempotent', async () => {
      const lease = await acquireAs(ownerId)
      await Promise.all([lease.release('Complete'), lease.release('Failed')])
      await lease.release('Failed')
      expect(stored()).to.include({ status: 'Complete', rev: 2 })
      expect(store.writes).to.deep.equal(['chatSession.update', 'chatSession.updateOne'])
    })

    it('restorePrevious puts back the status and failReason the session had before acquire', async () => {
      store.session(sessionId)!.set({ status: 'Failed', failReason: 'boom' })
      const lease = await acquireAs(ownerId)
      expect(stored()).to.include({ status: 'Inprogress' })
      await lease.release('Complete', { restorePrevious: true })
      expect(stored()).to.include({ activeRun: null, status: 'Failed', failReason: 'boom', rev: 2 })
    })

    it('restorePrevious does not put back a crashed run’s Inprogress, and a plain release ignores the previous state', async () => {
      store.session(sessionId)!.set({ status: 'Inprogress' })
      const crashed = await acquireAs(ownerId)
      await crashed.release('Complete', { restorePrevious: true })
      expect(stored()).to.include({ status: 'Complete' })

      store.session(sessionId)!.set({ status: 'Failed', failReason: 'boom' })
      const ran = await acquireAs(ownerId)
      await ran.release('Complete')
      expect(stored()).to.include({ status: 'Complete' })
      expect((stored() as Record<string, unknown>).failReason).to.equal(undefined)
    })

    it('release can be retried after a failed write', async () => {
      const lease = await acquireAs(ownerId)
      const updateOne = ChatSession.updateOne as unknown as sinon.SinonStub
      updateOne.onCall(updateOne.callCount).rejects(new Error('blip'))
      await lease.release('Complete').catch(() => undefined)
      expect(stored().activeRun).to.not.equal(null)
      await lease.release('Complete')
      expect(stored().activeRun).to.equal(null)
    })

    it('LS-05: a stale handle cannot release the lease of a newer run', async () => {
      const first = await acquireAs(ownerId)
      clock.advance(LEASE_TTL_MS + 1)
      const second = await acquireAs(guestId)
      await first.release('Failed')
      expect(stored().activeRun!.runId).to.equal(second.runId)
      expect(stored().status).to.equal('Inprogress')
    })

    it('ownershipFilter matches only while the run holds the lease', async () => {
      const first = await acquireAs(ownerId)
      expect((await ChatSession.updateOne(first.ownershipFilter(), { $set: { title: 'a' } })).matchedCount).to.equal(1)
      clock.advance(LEASE_TTL_MS + 1)
      await acquireAs(guestId)
      expect((await ChatSession.updateOne(first.ownershipFilter(), { $set: { title: 'b' } })).matchedCount).to.equal(0)
    })

    it('release also clears the lease of a deleted conversation', async () => {
      const lease = await acquireAs(ownerId)
      store.session(sessionId)!.set('isDeleted', true)
      await lease.release('Complete')
      expect(stored().activeRun).to.equal(null)
    })
  })

  describe('heartbeat', () => {
    let timers: sinon.SinonFakeTimers
    beforeEach(() => {
      timers = sinon.useFakeTimers({ now: T0, toFake: ['setInterval', 'clearInterval'] })
    })
    afterEach(() => {
      timers.restore()
    })

    const tick = async (ms: number) => {
      clock.advance(ms)
      await timers.tickAsync(ms)
    }

    it('LS-06: renews every 30 s for 300 s, keeping the lease alive, and writes only the expiry', async () => {
      const lease = await acquireAs(ownerId)
      const onLost = sinon.stub()
      lease.startHeartbeat(onLost)
      const rev = stored().rev
      const seen: number[] = []
      for (let i = 0; i < 10; i += 1) {
        await tick(30_000)
        seen.push(stored().activeRun!.leaseExpiresAt.getTime())
      }
      expect(seen).to.deep.equal(Array.from({ length: 10 }, (_, i) => T0 + (i + 1) * 30_000 + LEASE_TTL_MS))
      expect(stored().rev).to.equal(rev)
      expect(onLost.called).to.equal(false)
      await expectNoTakeover()
      lease.stopHeartbeat()
    })

    const expectNoTakeover = async () => {
      const error = await acquireAs(guestId).catch((e: unknown) => e)
      expect(error).to.be.instanceOf(ConversationBusyError)
    }

    it('calls onLost once and stops when the lease was taken over', async () => {
      const first = await acquireAs(ownerId)
      const onLost = sinon.stub()
      first.startHeartbeat(onLost)
      clock.advance(LEASE_TTL_MS + 1)
      await acquireAs(guestId)
      await tick(30_000)
      await tick(30_000)
      await tick(30_000)
      expect(onLost.calledOnce).to.equal(true)
    })

    it('calls onLost when the renew itself fails', async () => {
      const lease = await acquireAs(ownerId)
      const onLost = sinon.stub()
      lease.startHeartbeat(onLost)
      ;(ChatSession.updateOne as unknown as sinon.SinonStub).rejects(new Error('blip'))
      await tick(30_000)
      expect(onLost.calledOnce).to.equal(true)
    })

    it('stops renewing after stopHeartbeat and after release', async () => {
      const lease = await acquireAs(ownerId)
      lease.startHeartbeat(sinon.stub())
      lease.stopHeartbeat()
      const expiry = stored().activeRun!.leaseExpiresAt.getTime()
      await tick(60_000)
      expect(stored().activeRun!.leaseExpiresAt.getTime()).to.equal(expiry)

      lease.startHeartbeat(sinon.stub())
      await lease.release('Complete')
      expect(timers.countTimers()).to.equal(0)
    })
  })
})
