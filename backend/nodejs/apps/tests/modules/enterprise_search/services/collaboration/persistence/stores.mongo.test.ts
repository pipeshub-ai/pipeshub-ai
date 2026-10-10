import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { MongoAuditWriter } from '../../../../../../src/libs/audit/audit.writer'
import { AuditEvent } from '../../../../../../src/libs/audit/audit-event.schema'
import { ChatSessionReadState } from '../../../../../../src/modules/enterprise_search/schema/chat.session.read-state.schema'
import { MongoReadStateRepository } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/read-state.repository'
import { MongoNotificationPreferencesRepository } from '../../../../../../src/modules/notification/repository/notification-preferences.repository'
import { UserNotificationPreferences, MUTED_SESSIONS_MAX } from '../../../../../../src/modules/notification/schema/user-notification-preferences.schema'
import { Notifications } from '../../../../../../src/modules/notification/schema/notification.schema'

const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('audit writer, read state, preferences and coalesceKey against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const userId = new Types.ObjectId()
  const sessionId = new Types.ObjectId()
  const reads = new MongoReadStateRepository()
  const prefs = new MongoNotificationPreferencesRepository()
  const str = (id: Types.ObjectId) => String(id)

  before(async () => {
    await mongoose.connect(uri as string)
    await Promise.all([AuditEvent.init(), ChatSessionReadState.init(), UserNotificationPreferences.init(), Notifications.init()])
  })
  after(async () => {
    await Promise.all([
      AuditEvent.deleteMany({ orgId }),
      ChatSessionReadState.deleteMany({ orgId }),
      UserNotificationPreferences.deleteMany({ orgId }),
      Notifications.deleteMany({ orgId }),
    ])
    await mongoose.disconnect()
  })

  it('audit writer persists one row with aclVersion in after', async () => {
    const targetId = str(new Types.ObjectId())
    await new MongoAuditWriter().record({
      orgId,
      actorUserId: userId,
      action: 'chat.share',
      targetType: 'chatSession',
      targetId,
      principal: { principalType: 'user', principalId: 'u' },
      before: { accessLevel: 'read' },
      after: { accessLevel: 'write' },
      aclVersion: 3,
    })
    const rows = await AuditEvent.find({ orgId, targetId }).lean()
    expect(rows).to.have.length(1)
    expect(rows[0]!.after).to.deep.equal({ accessLevel: 'write', aclVersion: 3 })
    expect(rows[0]!.createdAt).to.be.instanceOf(Date)
  })

  it('audit writer does not throw when the write is invalid', async () => {
    await new MongoAuditWriter({ error: () => undefined }).record({ orgId, actorUserId: userId, action: '', targetType: 'x', targetId: '' })
    expect(await AuditEvent.countDocuments({ orgId, targetType: 'x' })).to.equal(0)
  })

  describe('read state', () => {
    it('upserts, only moves forward, and sets orgId once', async () => {
      await reads.markRead(str(orgId), str(userId), str(sessionId), 5)
      await reads.markRead(str(orgId), str(userId), str(sessionId), 3)
      let row = await ChatSessionReadState.findOne({ userId, sessionId }).lean()
      expect(row!.lastReadSeq).to.equal(5)
      expect(str(row!.orgId)).to.equal(str(orgId))
      await reads.markRead(str(orgId), str(userId), str(sessionId), 9)
      row = await ChatSessionReadState.findOne({ userId, sessionId }).lean()
      expect(row!.lastReadSeq).to.equal(9)
      expect(await ChatSessionReadState.countDocuments({ userId, sessionId })).to.equal(1)
    })

    it('concurrent writers converge on the maximum', async () => {
      const s = new Types.ObjectId()
      await Promise.all([1, 8, 4, 7, 2].map((n) => reads.markRead(str(orgId), str(userId), str(s), n)))
      expect((await ChatSessionReadState.findOne({ userId, sessionId: s }).lean())!.lastReadSeq).to.equal(8)
    })
  })

  describe('preferences', () => {
    const u = new Types.ObjectId()
    it('returns defaults when the doc is absent and does not create one', async () => {
      expect(await prefs.get(str(orgId), str(u))).to.deep.equal({
        email: { chatShared: true, ownershipTransferred: true, chatMentioned: false },
        inApp: { chatActivity: true, chatMentioned: true },
        mutedSessions: [],
        tipsSeen: [],
      })
      expect(await UserNotificationPreferences.countDocuments({ orgId, userId: u })).to.equal(0)
    })

    it('patches only the given flags and keeps the rest at their defaults', async () => {
      const res = await prefs.update(str(orgId), str(u), { email: { chatShared: false } })
      expect(res.email).to.deep.equal({ chatShared: false, ownershipTransferred: true, chatMentioned: false })
      expect(res.inApp).to.deep.equal({ chatActivity: true, chatMentioned: true })
      const res2 = await prefs.update(str(orgId), str(u), { inApp: { chatActivity: false } })
      expect(res2.email.chatShared).to.equal(false)
      expect(res2.inApp.chatActivity).to.equal(false)
      expect(await UserNotificationPreferences.countDocuments({ orgId, userId: u })).to.equal(1)
    })

    it('mute is idempotent and unmute removes', async () => {
      const s = str(new Types.ObjectId())
      expect(await prefs.muteSession(str(orgId), str(u), s)).to.equal('muted')
      expect(await prefs.muteSession(str(orgId), str(u), s)).to.equal('already_muted')
      expect((await prefs.get(str(orgId), str(u))).mutedSessions).to.deep.equal([s])
      await prefs.unmuteSession(str(orgId), str(u), s)
      expect((await prefs.get(str(orgId), str(u))).mutedSessions).to.deep.equal([])
    })

    it('mute creates the doc with defaults for a user who had none', async () => {
      const fresh = new Types.ObjectId()
      expect(await prefs.muteSession(str(orgId), str(fresh), str(new Types.ObjectId()))).to.equal('muted')
      const got = await prefs.get(str(orgId), str(fresh))
      expect(got.email.chatShared).to.equal(true)
      expect(got.mutedSessions).to.have.length(1)
    })

    it('mutedSessions is capped at 500, including under concurrency', async () => {
      const capped = new Types.ObjectId()
      const ids = Array.from({ length: MUTED_SESSIONS_MAX - 2 }, () => new Types.ObjectId())
      await UserNotificationPreferences.create({ orgId, userId: capped, mutedSessions: ids })
      const results = await Promise.all(Array.from({ length: 6 }, () => prefs.muteSession(str(orgId), str(capped), str(new Types.ObjectId()))))
      expect(results.filter((r) => r === 'muted')).to.have.length(2)
      expect(results.filter((r) => r === 'limit')).to.have.length(4)
      expect((await prefs.get(str(orgId), str(capped))).mutedSessions).to.have.length(MUTED_SESSIONS_MAX)
    })
  })

  describe('notification coalesceKey index', () => {
    const assignedTo = new Types.ObjectId()
    const make = (extra: Record<string, unknown>) => Notifications.create({ orgId, type: 'chat.activity', assignedTo, ...extra })

    it('allows one unread doc per coalesceKey and frees the key once read', async () => {
      const key = `chat.activity:${str(new Types.ObjectId())}`
      await make({ coalesceKey: key, status: 'unread' })
      let code: unknown
      await make({ coalesceKey: key, status: 'unread' }).catch((e: { code?: number }) => {
        code = e.code
      })
      expect(code).to.equal(11000)
      await Notifications.updateOne({ assignedTo, coalesceKey: key }, { $set: { status: 'read' } })
      await make({ coalesceKey: key, status: 'unread' })
    })

    it('docs without coalesceKey never collide', async () => {
      await make({})
      await make({})
      await make({ dedupeKey: undefined })
    })
  })
})
