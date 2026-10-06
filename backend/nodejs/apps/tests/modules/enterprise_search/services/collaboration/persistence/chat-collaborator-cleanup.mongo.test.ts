import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import sinon from 'sinon'
import { AuditEvent } from '../../../../../../src/libs/audit/audit-event.schema'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { UserNotificationPreferences } from '../../../../../../src/modules/notification/schema/user-notification-preferences.schema'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ChatSessionReadState } from '../../../../../../src/modules/enterprise_search/schema/chat.session.read-state.schema'
import { writeFilter } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters'
import { fencedWrite } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/fenced-write'
import { LeaseLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { MongoRunLeaseManager } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'
import { ChatCollaboratorCleanup } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/chat-collaborator-cleanup'
import { statusFor } from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-lifecycle'
import { appendMessages } from '../../../../../../src/modules/enterprise_search/utils/utils'
import { IChatSessionDocument, IMessage } from '../../../../../../src/modules/enterprise_search/types/conversation.interfaces'

// Runs against a replica set or a standalone mongod, whichever PCC_MONGO_URI names.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('ChatCollaboratorCleanup against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const otherOrgId = new Types.ObjectId()
  const ownerId = new Types.ObjectId()
  const bobId = new Types.ObjectId()
  const carolId = new Types.ObjectId()
  const previous = process.env.REPLICA_SET_AVAILABLE
  const orgs = [orgId, otherOrgId]

  const row = (userId: Types.ObjectId, accessLevel = 'read') => ({ principalType: 'user', userId, accessLevel })
  const teamRow = (teamId: string) => ({ principalType: 'team', teamId, accessLevel: 'read' })
  const chat = (org: Types.ObjectId, sharedWith: object[], extra: object = {}) =>
    ChatSession.create({
      orgId: org,
      userId: ownerId,
      initiator: ownerId,
      isShared: sharedWith.length > 0,
      sharedWith,
      rev: 3,
      aclVersion: 5,
      ...extra,
    })
  const reload = async (id: unknown) =>
    (await ChatSession.findById(id).select('+hiddenFor +archivedFor').lean())!

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    if (typeof hello.setName === 'string') process.env.REPLICA_SET_AVAILABLE = 'true'
    else delete process.env.REPLICA_SET_AVAILABLE
    await Promise.all([ChatSession.init(), ChatSessionMessage.init(), ChatSessionReadState.init()])
  })

  after(async () => {
    await ChatSessionMessage.deleteMany({ orgId: { $in: orgs } })
    await ChatSession.deleteMany({ orgId: { $in: orgs } })
    await ChatSessionReadState.deleteMany({ orgId: { $in: orgs } })
    await UserNotificationPreferences.deleteMany({ orgId: { $in: orgs } })
    await AuditEvent.deleteMany({ orgId: { $in: orgs } })
    await mongoose.disconnect()
    if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
    else process.env.REPLICA_SET_AVAILABLE = previous
  })

  beforeEach(async () => {
    await Promise.all([
      ChatSessionMessage.deleteMany({ orgId: { $in: orgs } }),
      ChatSession.deleteMany({ orgId: { $in: orgs } }),
      ChatSessionReadState.deleteMany({ orgId: { $in: orgs } }),
      UserNotificationPreferences.deleteMany({ orgId: { $in: orgs } }),
      AuditEvent.deleteMany({ orgId: { $in: orgs } }),
    ])
  })

  describe('removeUser', () => {
    it('pulls the user from every chat, recomputes isShared, bumps rev and aclVersion once, and leaves other rows and orgs alone', async () => {
      const onlyBob = await chat(orgId, [row(bobId)], { hiddenFor: [bobId], archivedFor: [bobId, carolId] })
      const withCarol = await chat(orgId, [row(bobId, 'write'), row(carolId), teamRow('t1')])
      const noBob = await chat(orgId, [row(carolId)])
      const elsewhere = await chat(otherOrgId, [row(bobId)])

      const result = await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect(result.removedFrom).to.equal(2)
      const a = await reload(onlyBob._id)
      expect(a.sharedWith).to.have.length(0)
      expect(a.isShared).to.equal(false)
      expect(a.hiddenFor).to.deep.equal([])
      expect(a.archivedFor!.map(String)).to.deep.equal([String(carolId)])
      expect(a).to.include({ rev: 4, aclVersion: 6 })

      const b = await reload(withCarol._id)
      expect(b.sharedWith.map((r) => String(r.userId ?? r.teamId))).to.deep.equal([String(carolId), 't1'])
      expect(b.isShared).to.equal(true)
      expect(b).to.include({ rev: 4, aclVersion: 6 })

      expect(await reload(noBob._id)).to.include({ rev: 3, aclVersion: 5 })
      const other = await reload(elsewhere._id)
      expect(other.sharedWith).to.have.length(1)
      expect(other).to.include({ isShared: true, rev: 3, aclVersion: 5 })
    })

    it('keeps the user\'s own messages, deletes their read states and preferences, and returns the owned shared chats', async () => {
      const shared = await chat(orgId, [row(bobId)])
      await ChatSessionMessage.create({
        orgId, sessionId: shared._id, seq: 1, messageType: 'user_query', content: 'hi', authorUserId: bobId, clientMessageId: 'm1',
      })
      await ChatSessionReadState.create([
        { orgId, userId: bobId, sessionId: shared._id, lastReadSeq: 1 },
        { orgId, userId: carolId, sessionId: shared._id, lastReadSeq: 1 },
      ])
      await UserNotificationPreferences.create([{ orgId, userId: bobId }, { orgId, userId: carolId }])
      await ChatSession.create([
        { orgId, userId: bobId, initiator: bobId, isShared: true, sharedWith: [row(carolId)] },
        { orgId, userId: bobId, initiator: bobId, isShared: true, sharedWith: [row(ownerId)] },
        { orgId, userId: bobId, initiator: bobId, isShared: false },
      ])

      const result = await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect(result.ownedSharedChats).to.equal(2)
      expect(await ChatSession.countDocuments({ orgId, userId: bobId })).to.equal(3)
      expect(await ChatSessionMessage.countDocuments({ sessionId: shared._id, authorUserId: bobId })).to.equal(1)
      expect((await ChatSessionReadState.find({ orgId }).lean()).map((r) => String(r.userId))).to.deep.equal([String(carolId)])
      expect((await UserNotificationPreferences.find({ orgId }).lean()).map((r) => String(r.userId))).to.deep.equal([String(carolId)])
    })

    it('writes one chat.offboard audit row naming the actor, and none on a no-op re-run', async () => {
      await chat(orgId, [row(bobId)])
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId), { actorUserId: String(ownerId), requestId: 'r1' })
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId), { actorUserId: String(ownerId) })

      const events = await AuditEvent.find({ orgId }).lean()
      expect(events).to.have.length(1)
      expect(events[0]).to.include({ action: 'chat.offboard', targetType: 'user', targetId: String(bobId), requestId: 'r1' })
      expect(String(events[0]!.actorUserId)).to.equal(String(ownerId))
      expect(events[0]!.after).to.include({ removedFrom: 1 })
    })

    it('is idempotent: a second run changes nothing', async () => {
      const id = (await chat(orgId, [row(bobId), row(carolId)], { activeRun: { runId: 'r', userId: bobId, startedAt: new Date(), leaseExpiresAt: new Date(Date.now() + 60_000) } }))._id
      await ChatSessionReadState.create({ orgId, userId: bobId, sessionId: id, lastReadSeq: 0 })
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))
      const first = await reload(id)

      const again = await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect(again.removedFrom).to.equal(0)
      expect(await reload(id)).to.deep.equal(first)
      expect(first).to.include({ rev: 5, aclVersion: 6 })
    })

    it('a user re-added later with the same id starts with no shared chats (LC-04)', async () => {
      await chat(orgId, [row(bobId)])
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))
      expect(await ChatSession.countDocuments({ orgId, 'sharedWith.userId': bobId })).to.equal(0)
    })

    it('clears a lease the user holds, so the in-flight turn\'s next fenced write is refused and nothing lands', async () => {
      const guest = await chat(orgId, [row(bobId, 'write')])
      const sessionId = String(guest._id)
      const caller = { userId: String(bobId), orgId: String(orgId), teamIds: [] as string[] }
      const manager = new MongoRunLeaseManager({ clock: new FixedClock(Date.now()) })
      const lease = await manager.acquire(sessionId, caller, writeFilter(caller, { kind: 'chat', conversationId: sessionId }, { collab: true }))

      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect((await reload(guest._id)).activeRun).to.equal(null)
      const message = { messageType: 'user_query', content: 'late', authorUserId: bobId, clientMessageId: 'late' } as IMessage
      const refused = await fencedWrite(lease, (db) => appendMessages(guest._id as Types.ObjectId, orgId, [message], db)).catch((e: unknown) => e)
      expect(refused).to.be.instanceOf(LeaseLostError)
      expect(await ChatSessionMessage.countDocuments({ sessionId: guest._id })).to.equal(0)
      expect(await lease.release('Failed').then(() => 'ok')).to.equal('ok')
    })

    it('clears a lease the owner holds on their own chat', async () => {
      const own = await chat(orgId, [row(carolId)], { userId: bobId, initiator: bobId, activeRun: { runId: 'r', userId: bobId, startedAt: new Date(), leaseExpiresAt: new Date(Date.now() + 60_000) } })
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))
      expect((await reload(own._id)).activeRun).to.equal(null)
    })

    it('does not clear another org\'s lease for the same user id', async () => {
      const run = { runId: 'r', userId: bobId, startedAt: new Date(), leaseExpiresAt: new Date(Date.now() + 60_000) }
      const elsewhere = await chat(otherOrgId, [], { activeRun: run })
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))
      expect((await reload(elsewhere._id)).activeRun).to.not.equal(null)
    })

    it('writes the audit row through the injected writer', async () => {
      await chat(orgId, [row(bobId)])
      const record = sinon.stub().resolves()
      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId), { actorUserId: String(ownerId), requestId: 'r2' }, { record })
      expect(record.calledOnce).to.equal(true)
      expect(record.firstCall.args[0]).to.include({ action: 'chat.offboard', targetType: 'user', targetId: String(bobId), requestId: 'r2' })
    })

    it('survives a failing audit write', async () => {
      await chat(orgId, [row(bobId)])
      const stub = sinon.stub(AuditEvent, 'create').rejects(new Error('audit down'))
      try {
        const result = await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))
        expect(result.removedFrom).to.equal(1)
        expect(stub.calledOnce).to.equal(true)
      } finally {
        stub.restore()
      }
    })

    it('pulls ids left in hiddenFor/archivedFor without an access row, with no rev bump, and does not count them as removed', async () => {
      const left = await chat(orgId, [row(carolId)], { hiddenFor: [bobId, carolId], archivedFor: [bobId] })
      const elsewhere = await chat(otherOrgId, [], { hiddenFor: [bobId] })

      const result = await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect(result.removedFrom).to.equal(0)
      const a = await reload(left._id)
      expect(a.hiddenFor!.map(String)).to.deep.equal([String(carolId)])
      expect(a.archivedFor).to.deep.equal([])
      expect(a).to.include({ rev: 3, aclVersion: 5, isShared: true })
      expect((await reload(elsewhere._id)).hiddenFor).to.have.length(1)
      expect(await AuditEvent.countDocuments({ orgId })).to.equal(1)
    })

    it('sets the stopped status when it clears the removed user\'s run, and leaves another user\'s run alone', async () => {
      const run = (userId: Types.ObjectId) => ({ runId: 'r', userId, startedAt: new Date(), leaseExpiresAt: new Date(Date.now() + 60_000) })
      const mine = await chat(orgId, [row(bobId, 'write')], { status: 'Inprogress', activeRun: run(bobId) })
      const theirs = await chat(orgId, [row(bobId, 'write')], { status: 'Inprogress', activeRun: run(carolId) })

      await ChatCollaboratorCleanup.removeUser(String(orgId), String(bobId))

      expect(await reload(mine._id)).to.include({ status: statusFor('stopped'), activeRun: null })
      const other = await reload(theirs._id)
      expect(other.status).to.equal('Inprogress')
      expect(other.activeRun).to.not.equal(null)
    })
  })

  describe('removeTeam', () => {
    it('pulls the team row, recomputes isShared, bumps once, leaves user rows and other orgs, and re-runs as a no-op (LC-13)', async () => {
      const teamOnly = await chat(orgId, [teamRow('t1')])
      const mixed = await chat(orgId, [teamRow('t1'), row(carolId), teamRow('t2')])
      const other = await chat(otherOrgId, [teamRow('t1')])

      const result = await ChatCollaboratorCleanup.removeTeam(String(orgId), 't1')
      const again = await ChatCollaboratorCleanup.removeTeam(String(orgId), 't1')

      expect(result.removedFrom).to.equal(2)
      expect(again.removedFrom).to.equal(0)
      const a = await reload(teamOnly._id)
      expect(a).to.include({ isShared: false, rev: 4, aclVersion: 6 })
      expect(a.sharedWith).to.have.length(0)
      const b = await reload(mixed._id)
      expect(b.sharedWith.map((r) => String(r.userId ?? r.teamId))).to.deep.equal([String(carolId), 't2'])
      expect(b).to.include({ isShared: true, rev: 4, aclVersion: 6 })
      expect(await reload(other._id)).to.include({ isShared: true, rev: 3, aclVersion: 5 })
    })
  })
})
