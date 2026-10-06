import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { writeFilter } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters'
import { ConversationBusyError } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { fencedWrite } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/fenced-write'
import { LEASE_TTL_MS } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease-filters'
import { LeaseLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { MongoRunLeaseManager } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'

// Needs a replica-set mongod, e.g. PCC_MONGO_URI='mongodb://127.0.0.1:27017/lease_test?directConnection=true'.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('MongoRunLeaseManager against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const ownerId = new Types.ObjectId()
  const clock = new FixedClock(Date.now())
  const manager = new MongoRunLeaseManager({ clock })
  const caller = { userId: String(ownerId), orgId: String(orgId), teamIds: [] as string[] }
  let sessionId: string
  const previous = process.env.REPLICA_SET_AVAILABLE
  let replicaSet = false

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    replicaSet = Boolean(hello.setName)
    await ChatSession.init()
  })

  after(async () => {
    await ChatSession.deleteMany({ orgId })
    await mongoose.disconnect()
    if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
    else process.env.REPLICA_SET_AVAILABLE = previous
  })

  beforeEach(async () => {
    const session = await ChatSession.create({ orgId, userId: ownerId, initiator: ownerId })
    sessionId = String(session._id)
  })

  const filter = () => writeFilter(caller, { kind: 'chat', conversationId: sessionId }, { collab: true })

  it('LS-02: exactly one of 20 concurrent acquires wins', async () => {
    const results = await Promise.allSettled(Array.from({ length: 20 }, () => manager.acquire(sessionId, caller, filter())))
    expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1)
    for (const r of results) {
      if (r.status === 'rejected') expect(r.reason).to.be.instanceOf(ConversationBusyError)
    }
    expect((await ChatSession.findById(sessionId))!.rev).to.equal(1)
  })

  it('takes over an expired lease, and the old handle can neither renew nor release it', async () => {
    const first = await manager.acquire(sessionId, caller, filter())
    clock.advance(LEASE_TTL_MS + 1)
    const second = await manager.acquire(sessionId, caller, filter())
    expect(await first.renew()).to.equal(false)
    await first.release('Failed')
    const doc = await ChatSession.findById(sessionId)
    expect(doc!.activeRun!.runId).to.equal(second.runId)
    await second.release('Complete')
    await second.release('Complete')
    const released = await ChatSession.findById(sessionId)
    expect(released).to.include({ status: 'Complete', rev: 3 })
    expect(released!.activeRun).to.equal(null)
  })

  it('fencedWrite runs in a transaction and commits while the lease is held', async function () {
    if (!replicaSet) this.skip()
    process.env.REPLICA_SET_AVAILABLE = 'true'
    const lease = await manager.acquire(sessionId, caller, filter())
    await fencedWrite(lease, (dbSession) =>
      ChatSession.updateOne({ _id: sessionId }, { $set: { title: 'fenced' } }, { session: dbSession ?? undefined }),
    )
    expect((await ChatSession.findById(sessionId))!.title).to.equal('fenced')
  })

  it('fencedWrite rolls back and throws LeaseLostError once the lease was taken over', async function () {
    if (!replicaSet) this.skip()
    process.env.REPLICA_SET_AVAILABLE = 'true'
    const stale = await manager.acquire(sessionId, caller, filter())
    clock.advance(LEASE_TTL_MS + 1)
    await manager.acquire(sessionId, caller, filter())
    const error = await fencedWrite(stale, (dbSession) =>
      ChatSession.updateOne({ _id: sessionId }, { $set: { title: 'stale' } }, { session: dbSession ?? undefined }),
    ).catch((e: unknown) => e)
    expect(error).to.be.instanceOf(LeaseLostError)
    expect((await ChatSession.findById(sessionId))!.title).to.not.equal('stale')
  })
})
