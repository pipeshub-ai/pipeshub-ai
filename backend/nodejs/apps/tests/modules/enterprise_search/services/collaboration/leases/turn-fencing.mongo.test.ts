import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { writeFilter } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters'
import { ConversationBusyError } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { isDuplicateCreationKey } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/turn-preconditions'
import { fencedWrite } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/fenced-write'
import { LEASE_TTL_MS } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease-filters'
import { LeaseLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { MongoRunLeaseManager } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'
import { TurnLifecycle } from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-lifecycle'
import Citation from '../../../../../../src/modules/enterprise_search/schema/citation.schema'
import { appendMessages, handleRegenerationSuccess, patchTurnSession, saveCompletedTurn } from '../../../../../../src/modules/enterprise_search/utils/utils'
import { IChatSessionDocument, IMessage } from '../../../../../../src/modules/enterprise_search/types/conversation.interfaces'

// Runs against a replica set or a standalone mongod, whichever PCC_MONGO_URI names; the fenced path follows the topology.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('turn fencing against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const ownerId = new Types.ObjectId()
  const guestId = new Types.ObjectId()
  const clock = new FixedClock(Date.now())
  const manager = new MongoRunLeaseManager({ clock })
  const callerOf = (id: Types.ObjectId) => ({ userId: String(id), orgId: String(orgId), teamIds: [] as string[] })
  const previous = process.env.REPLICA_SET_AVAILABLE
  let replicaSet = false
  let session: IChatSessionDocument
  let sessionId: string

  const filter = (id: Types.ObjectId) => writeFilter(callerOf(id), { kind: 'chat', conversationId: sessionId }, { collab: true })
  const acquire = (id: Types.ObjectId = ownerId) => manager.acquire(sessionId, callerOf(id), filter(id))
  const question = (author: Types.ObjectId, clientMessageId: string): IMessage =>
    ({ messageType: 'user_query', content: 'q', authorUserId: author, clientMessageId }) as IMessage

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    replicaSet = typeof hello.setName === 'string'
    if (replicaSet) process.env.REPLICA_SET_AVAILABLE = 'true'
    else delete process.env.REPLICA_SET_AVAILABLE
    await Promise.all([ChatSession.init(), ChatSessionMessage.init(), Citation.init()])
  })

  after(async () => {
    await Citation.deleteMany({ 'metadata.orgId': orgId })
    await ChatSessionMessage.deleteMany({ orgId })
    await ChatSession.deleteMany({ orgId })
    await mongoose.disconnect()
    if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
    else process.env.REPLICA_SET_AVAILABLE = previous
  })

  beforeEach(async () => {
    await Citation.deleteMany({ 'metadata.orgId': orgId })
    session = (await ChatSession.create({
      orgId,
      userId: ownerId,
      initiator: ownerId,
      isShared: true,
      sharedWith: [{ principalType: 'user', userId: guestId, accessLevel: 'write' }],
    })) as unknown as IChatSessionDocument
    sessionId = String(session._id)
  })

  it('two concurrent acquires by different users: one wins, the other is BUSY naming the winner', async () => {
    const results = await Promise.allSettled([acquire(ownerId), acquire(guestId)])
    const won = results.filter((r) => r.status === 'fulfilled')
    const lost = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected')
    expect(won).to.have.length(1)
    expect(lost[0]!.reason).to.be.instanceOf(ConversationBusyError)
    const holder = (await ChatSession.findById(sessionId).lean())!.activeRun!
    expect(String(holder.userId)).to.equal(String(lost[0]!.reason.publicDetails.activeRun.userId))
  })

  it('after a takeover the stale run can append nothing and patch nothing; the new run can', async () => {
    const stale = await acquire(ownerId)
    clock.advance(LEASE_TTL_MS + 1)
    const fresh = await acquire(guestId)

    const staleAppend = await fencedWrite(stale, (db) =>
      appendMessages(session._id, orgId, [question(ownerId, 'stale')], db),
    ).catch((e: unknown) => e)
    expect(staleAppend).to.be.instanceOf(LeaseLostError)
    const stalePatch = await patchTurnSession(session, { status: 'Complete', lastActivityAt: 1 }, { lease: stale })
    expect(stalePatch).to.equal(false)

    await fencedWrite(fresh, (db) => appendMessages(session._id, orgId, [question(guestId, 'fresh')], db))
    expect(await patchTurnSession(session, { status: 'Complete', lastActivityAt: 2 }, { lease: fresh })).to.equal(true)

    const rows = await ChatSessionMessage.find({ sessionId: session._id }).lean()
    expect(rows.map((r) => r.clientMessageId)).to.deep.equal(['fresh'])
    expect((await ChatSession.findById(sessionId).lean())!).to.include({ status: 'Complete', lastActivityAt: 2 })
  })

  it('a patch on a deleted conversation matches nothing even while the lease is held (CL-01)', async () => {
    const lease = await acquire(guestId)
    await ChatSession.updateOne({ _id: session._id }, { $set: { isDeleted: true } })
    expect(await patchTurnSession(session, { status: 'Complete', lastActivityAt: 3 }, { lease })).to.equal(false)
  })

  it('the per-author clientMessageId index raises E11000 for a repeat, but not for another author', async () => {
    const lease = await acquire(guestId)
    await fencedWrite(lease, (db) => appendMessages(session._id, orgId, [question(guestId, 'k')], db))
    await fencedWrite(lease, (db) => appendMessages(session._id, orgId, [question(ownerId, 'k')], db))
    const repeat = (await fencedWrite(lease, (db) =>
      appendMessages(session._id, orgId, [question(guestId, 'k')], db),
    ).catch((e: unknown) => e)) as { code?: number; message?: string }
    expect(repeat.code).to.equal(11000)
    // insertMany's MongoBulkWriteError has no top-level keyPattern: the handler's duplicate check relies on the index name.
    expect(repeat.message).to.match(/clientMessageId/)
    expect(await ChatSessionMessage.countDocuments({ sessionId: session._id })).to.equal(2)
  })

  it('the creationKey index raises an E11000 that isDuplicateCreationKey recognises', async () => {
    const fields = { orgId, userId: guestId, initiator: guestId, creationKey: 'first-send' }
    await ChatSession.create(fields)
    const error = await ChatSession.create(fields).catch((e: unknown) => e)
    expect(isDuplicateCreationKey(error)).to.equal(true)
  })

  it('a turn that never ran puts back the Failed status and failReason the session had', async () => {
    await ChatSession.updateOne({ _id: session._id }, { $set: { status: 'Failed', failReason: 'earlier' } })
    const lease = await acquire(guestId)
    expect((await ChatSession.findById(sessionId).lean())!.status).to.equal('Inprogress')
    await new TurnLifecycle(lease).settle('unstarted')
    expect(await ChatSession.findById(sessionId).lean()).to.deep.include({ activeRun: null, status: 'Failed', failReason: 'earlier' })

    const ran = await acquire(guestId)
    await new TurnLifecycle(ran).settle('completed')
    const after = (await ChatSession.findById(sessionId).lean())!
    expect(after.status).to.equal('Complete')
    expect(after).to.not.have.property('failReason')
  })

  const answer = {
    answer: 'a',
    status: 'success',
    citations: [{ content: 'c', chunkIndex: 0, citationType: 'vector_db|document', metadata: { recordId: 'r', origin: 'UPLOAD', recordName: 'n', mimeType: 'text/plain' } }],
  } as never
  const citationCount = () => Citation.countDocuments({ 'metadata.orgId': orgId })

  it('N2: a fenced-out run saves no citation and no answer; the live run saves both', async () => {
    const stale = await acquire(ownerId)
    clock.advance(LEASE_TTL_MS + 1)
    const fresh = await acquire(guestId)

    const lost = await saveCompletedTurn(session, answer, String(orgId), { run: { lease: stale } }).catch((e: unknown) => e)
    expect(lost).to.be.instanceOf(LeaseLostError)
    expect(await citationCount()).to.equal(0)
    expect(await ChatSessionMessage.countDocuments({ sessionId: session._id })).to.equal(0)

    await saveCompletedTurn(session, answer, String(orgId), { run: { lease: fresh } })
    expect(await citationCount()).to.equal(1)
    expect(await ChatSessionMessage.countDocuments({ sessionId: session._id })).to.equal(1)
  })

  it('N2: a fenced-out regenerate saves no citation and deletes no stale card; the live run does both', async () => {
    const [bot, card] = await appendMessages(
      session._id,
      orgId,
      [{ messageType: 'bot_response', content: 'old' } as IMessage, { messageType: 'tool_call', content: 'card' } as IMessage],
      null,
    )
    const stale = await acquire(ownerId)
    clock.advance(LEASE_TTL_MS + 1)
    const fresh = await acquire(guestId)
    const regenerate = (lease: typeof stale) =>
      handleRegenerationSuccess(answer, session, bot!._id, String(orgId), null, undefined, undefined, [card!._id], { lease })

    const lost = await regenerate(stale).catch((e: unknown) => e)
    expect(lost).to.be.instanceOf(LeaseLostError)
    expect(await citationCount()).to.equal(0)
    expect(await ChatSessionMessage.countDocuments({ _id: card!._id })).to.equal(1)
    expect((await ChatSessionMessage.findById(bot!._id).lean())!.content).to.equal('old')

    await regenerate(fresh)
    expect(await citationCount()).to.equal(1)
    expect(await ChatSessionMessage.countDocuments({ _id: card!._id })).to.equal(0)
    expect((await ChatSessionMessage.findById(bot!._id).lean())!.content).to.equal('a')
  })

  it('N2: the stale-card delete is scoped to the conversation', async () => {
    const other = await ChatSession.create({ orgId, userId: ownerId, initiator: ownerId })
    const [foreign] = await appendMessages(other._id, orgId, [{ messageType: 'tool_call', content: 'x' } as IMessage], null)
    const [bot] = await appendMessages(session._id, orgId, [{ messageType: 'bot_response', content: 'old' } as IMessage], null)
    const lease = await acquire(ownerId)
    await handleRegenerationSuccess(answer, session, bot!._id, String(orgId), null, undefined, undefined, [foreign!._id], { lease })
    expect(await ChatSessionMessage.countDocuments({ _id: foreign!._id })).to.equal(1)
  })
})
