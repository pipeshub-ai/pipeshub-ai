import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { MongoCollaboratorRepository } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/collaborator.repository'
import { SHARED_WITH_MAX } from '../../../../../../src/modules/enterprise_search/constants/constants'

// Needs $expr, pipeline updates and arrayFilters, which no in-memory fake models; skipped without PCC_MONGO_URI.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('collaborator.repository against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const owner = new Types.ObjectId()
  const b = new Types.ObjectId()
  const c = new Types.ObjectId()
  const d = new Types.ObjectId()
  const clock = new FixedClock(Date.UTC(2026, 9, 2))
  const repo = new MongoCollaboratorRepository(clock)
  let sid: string

  const user = (id: Types.ObjectId) => ({ type: 'user' as const, userId: String(id) })
  const scope = () => ({ sessionId: sid, orgId: String(orgId), ownerId: String(owner) })
  const anyScope = () => ({ sessionId: sid, orgId: String(orgId) })
  const load = async () => (await ChatSession.findById(sid).select('+hiddenFor +archivedFor').lean())!
  const mk = async (extra: Record<string, unknown> = {}) => {
    const s = await ChatSession.create({ orgId, userId: owner, initiator: owner, ...extra })
    sid = String(s._id)
  }
  const addInput = (id: Types.ObjectId, accessLevel: 'read' | 'write' = 'read') => ({
    principal: user(id),
    accessLevel,
    addedBy: String(owner),
  })

  before(async () => {
    await mongoose.connect(uri as string)
    await ChatSession.init()
  })
  after(async () => {
    await ChatSession.deleteMany({ orgId })
    await mongoose.disconnect()
  })
  beforeEach(() => mk())

  describe('add', () => {
    it('pushes the row, sets isShared, bumps rev and aclVersion once', async () => {
      const res = await repo.add(scope(), addInput(b, 'write'))
      expect(res).to.include({ status: 'added', rev: 1, aclVersion: 1, isShared: true })
      const s = await load()
      expect(s.sharedWith).to.have.length(1)
      expect(s.sharedWith![0]).to.include({ principalType: 'user', accessLevel: 'write' })
      expect(String(s.sharedWith![0]!.userId)).to.equal(String(b))
      expect(String(s.sharedWith![0]!.addedBy)).to.equal(String(owner))
      expect(s.rev).to.equal(1)
      expect(s.aclVersion).to.equal(1)
    })

    it('adds a team row without touching hiddenFor', async () => {
      await mk({ isShared: true, hiddenFor: [b] })
      const res = await repo.add(scope(), { principal: { type: 'team', teamId: 'T1' }, accessLevel: 'read', addedBy: String(owner) })
      expect(res.status).to.equal('added')
      const s = await load()
      expect(s.sharedWith![0]).to.include({ principalType: 'team', teamId: 'T1' })
      expect(s.hiddenFor!.map(String)).to.deep.equal([String(b)])
    })

    it('reports already_present and changes nothing (rev unchanged)', async () => {
      await repo.add(scope(), addInput(b))
      const res = await repo.add(scope(), addInput(b, 'write'))
      expect(res).to.deep.equal({ status: 'already_present' })
      const s = await load()
      expect(s.sharedWith).to.have.length(1)
      expect(s.sharedWith![0]!.accessLevel).to.equal('read')
      expect(s.rev).to.equal(1)
    })

    it('re-share un-hides and un-archives the recipient but leaves others', async () => {
      await mk({ isShared: true, hiddenFor: [b, c], archivedFor: [b, c] })
      await repo.add(scope(), addInput(b))
      const s = await load()
      expect(s.hiddenFor!.map(String)).to.deep.equal([String(c)])
      expect(s.archivedFor!.map(String)).to.deep.equal([String(c)])
    })

    it('not_found for another owner, another org or an unknown session', async () => {
      expect(await repo.add({ ...scope(), ownerId: String(b) }, addInput(c))).to.deep.equal({ status: 'not_found' })
      expect(await repo.add({ ...scope(), orgId: String(new Types.ObjectId()) }, addInput(c))).to.deep.equal({ status: 'not_found' })
      expect(await repo.add({ ...scope(), sessionId: String(new Types.ObjectId()) }, addInput(c))).to.deep.equal({ status: 'not_found' })
    })

    it('without ownerId (an editor invite) the owner filter is not applied', async () => {
      expect((await repo.add(anyScope(), addInput(b))).status).to.equal('added')
    })

    it('at the cap: the 201st is refused with limit, rev unchanged', async () => {
      const rows = Array.from({ length: SHARED_WITH_MAX }, () => ({ principalType: 'user', userId: new Types.ObjectId(), accessLevel: 'read' }))
      await mk({ isShared: true, sharedWith: rows, rev: 5 })
      const res = await repo.add(scope(), addInput(b))
      expect(res).to.deep.equal({ status: 'limit', max: SHARED_WITH_MAX })
      const s = await load()
      expect(s.sharedWith).to.have.length(SHARED_WITH_MAX)
      expect(s.rev).to.equal(5)
    })

    it('concurrent adds at the cap never exceed 200', async () => {
      const rows = Array.from({ length: SHARED_WITH_MAX - 3 }, () => ({ principalType: 'user', userId: new Types.ObjectId(), accessLevel: 'read' }))
      await mk({ isShared: true, sharedWith: rows })
      const newcomers = Array.from({ length: 12 }, () => new Types.ObjectId())
      const results = await Promise.all(newcomers.map((id) => repo.add(scope(), addInput(id))))
      expect(results.filter((r) => r.status === 'added')).to.have.length(3)
      expect(results.filter((r) => r.status === 'limit')).to.have.length(9)
      expect((await load()).sharedWith).to.have.length(SHARED_WITH_MAX)
    })

    it('concurrent adds of two users keep both rows, no duplicate', async () => {
      await Promise.all([repo.add(scope(), addInput(b)), repo.add(scope(), addInput(c)), repo.add(scope(), addInput(b))])
      const s = await load()
      expect(s.sharedWith!.map((r) => String(r.userId)).sort()).to.deep.equal([String(b), String(c)].sort())
      expect(s.isShared).to.equal(true)
    })

    it('first share of an owner-archived chat converts isArchived to archivedFor:[owner]', async () => {
      await mk({ isArchived: true, archivedBy: owner })
      await repo.add(scope(), addInput(b))
      const s = await load()
      expect(s.isArchived).to.equal(false)
      expect(s.archivedBy).to.equal(undefined)
      expect(s.archivedFor!.map(String)).to.deep.equal([String(owner)])
    })

    it('a second share does not touch archive state, and a recipient never inherits the owner archive', async () => {
      await mk({ isArchived: true, archivedBy: owner })
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), addInput(c))
      const s = await load()
      expect(s.archivedFor!.map(String)).to.deep.equal([String(owner)])
      expect(s.archivedFor!.map(String)).to.not.include(String(b))
    })

    it('an unarchived chat keeps archivedFor empty on first share', async () => {
      await repo.add(scope(), addInput(b))
      const s = await load()
      expect(s.isArchived).to.equal(false)
      expect(s.archivedFor).to.have.length(0)
    })
  })

  describe('changeLevel', () => {
    it('changes only the targeted row, leaves other rows untouched, bumps once', async () => {
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), addInput(c))
      await repo.add(scope(), { principal: { type: 'team', teamId: 'T1' }, accessLevel: 'read', addedBy: String(owner) })
      const before = await load()
      const res = await repo.changeLevel(scope(), user(b), 'write')
      expect(res).to.include({ status: 'applied', rev: 4, aclVersion: 4 })
      const s = await load()
      const row = (id: Types.ObjectId) => s.sharedWith!.find((r) => String(r.userId) === String(id))!
      expect(row(b).accessLevel).to.equal('write')
      expect(row(b).updatedAt).to.be.instanceOf(Date)
      expect(row(c)).to.deep.equal(before.sharedWith!.find((r) => String(r.userId) === String(c)))
      expect(s.sharedWith!.find((r) => r.teamId === 'T1')).to.deep.equal(before.sharedWith!.find((r) => r.teamId === 'T1'))
    })

    it('changes a team row by teamId', async () => {
      await repo.add(scope(), { principal: { type: 'team', teamId: 'T1' }, accessLevel: 'read', addedBy: String(owner) })
      expect((await repo.changeLevel(scope(), { type: 'team', teamId: 'T1' }, 'write')).status).to.equal('applied')
      expect((await load()).sharedWith![0]!.accessLevel).to.equal('write')
    })

    it('same level is unchanged, absent principal is absent, both leave rev alone (DB-06)', async () => {
      await repo.add(scope(), addInput(b))
      expect(await repo.changeLevel(scope(), user(b), 'read')).to.deep.equal({ status: 'unchanged' })
      expect(await repo.changeLevel(scope(), user(c), 'write')).to.deep.equal({ status: 'absent' })
      expect(await repo.changeLevel({ ...scope(), sessionId: String(new Types.ObjectId()) }, user(b), 'write')).to.deep.equal({ status: 'not_found' })
      expect((await load()).rev).to.equal(1)
    })

    it('is owner-gated', async () => {
      await repo.add(scope(), addInput(b))
      expect((await repo.changeLevel({ ...scope(), ownerId: String(c) }, user(b), 'write')).status).to.equal('not_found')
    })
  })

  describe('remove', () => {
    it('pulls the row and its hidden/archived entries, keeps isShared while rows remain', async () => {
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), addInput(c))
      await ChatSession.updateOne({ _id: sid }, { $set: { hiddenFor: [b, c], archivedFor: [b, c] } })
      const res = await repo.remove(scope(), user(b))
      expect(res).to.include({ status: 'applied', rev: 3, aclVersion: 3, isShared: true })
      const s = await load()
      expect(s.sharedWith!.map((r) => String(r.userId))).to.deep.equal([String(c)])
      expect(s.hiddenFor!.map(String)).to.deep.equal([String(c)])
      expect(s.archivedFor!.map(String)).to.deep.equal([String(c)])
    })

    it('removing the last principal recomputes isShared to false', async () => {
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), { principal: { type: 'team', teamId: 'T1' }, accessLevel: 'read', addedBy: String(owner) })
      expect((await repo.remove(scope(), { type: 'team', teamId: 'T1' })).status).to.equal('applied')
      expect((await load()).isShared).to.equal(true)
      const last = await repo.remove(scope(), user(b))
      expect(last).to.include({ status: 'applied', isShared: false })
      expect((await load()).sharedWith).to.have.length(0)
    })

    it('an absent principal is a no-op: rev unchanged (DB-06)', async () => {
      await repo.add(scope(), addInput(b))
      expect(await repo.remove(scope(), user(c))).to.deep.equal({ status: 'absent' })
      expect(await repo.remove({ ...scope(), sessionId: String(new Types.ObjectId()) }, user(c))).to.deep.equal({ status: 'not_found' })
      expect((await load()).rev).to.equal(1)
    })

    it('removing a user does not remove a team row with the same text id, and vice versa', async () => {
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), { principal: { type: 'team', teamId: String(b) }, accessLevel: 'read', addedBy: String(owner) })
      await repo.remove(scope(), { type: 'team', teamId: String(b) })
      const s = await load()
      expect(s.sharedWith).to.have.length(1)
      expect(String(s.sharedWith![0]!.userId)).to.equal(String(b))
    })

    it('handles legacy rows without principalType', async () => {
      await mk({ isShared: true, sharedWith: [{ userId: b, accessLevel: 'read' }] })
      expect((await repo.remove(scope(), user(b))).status).to.equal('applied')
      expect((await load()).isShared).to.equal(false)
    })
  })

  describe('transfer', () => {
    beforeEach(async () => {
      await repo.add(scope(), addInput(b, 'write'))
      await repo.add(scope(), addInput(c, 'write'))
      await repo.add(scope(), addInput(d, 'read'))
    })
    const xfer = (to: Types.ObjectId, from: Types.ObjectId = owner) =>
      repo.transfer(anyScope(), { fromUserId: String(from), toUserId: String(to) })

    it('swaps owner and initiator, demotes the old owner to write, drops the new owner row, records history', async () => {
      const res = await xfer(b)
      expect(res).to.include({ status: 'applied', rev: 4, aclVersion: 4 })
      const s = await load()
      expect(String(s.userId)).to.equal(String(b))
      expect(String(s.initiator)).to.equal(String(b))
      const byUser = new Map(s.sharedWith!.map((r) => [String(r.userId), r]))
      expect(byUser.has(String(b))).to.equal(false)
      expect(byUser.get(String(owner))).to.include({ accessLevel: 'write', principalType: 'user' })
      expect(byUser.get(String(c))!.accessLevel).to.equal('write')
      expect(byUser.get(String(d))!.accessLevel).to.equal('read')
      expect(s.sharedWith).to.have.length(3)
      expect(s.ownershipHistory).to.have.length(1)
      expect(String(s.ownershipHistory![0]!.fromUserId)).to.equal(String(owner))
      expect(String(s.ownershipHistory![0]!.toUserId)).to.equal(String(b))
    })

    it('two concurrent transfers: exactly one applies', async () => {
      const results = await Promise.all([xfer(b), xfer(c)])
      expect(results.filter((r) => r.status === 'applied')).to.have.length(1)
      expect(results.filter((r) => r.status === 'no_match')).to.have.length(1)
      const s = await load()
      expect(s.ownershipHistory).to.have.length(1)
      expect(s.sharedWith).to.have.length(3)
    })

    it('does not match a read-only target, a non-collaborator, or the wrong current owner', async () => {
      expect((await xfer(d)).status).to.equal('no_match')
      expect((await xfer(new Types.ObjectId())).status).to.equal('no_match')
      expect((await xfer(b, c)).status).to.equal('no_match')
      const s = await load()
      expect(String(s.userId)).to.equal(String(owner))
      expect(s.rev).to.equal(3)
    })

    it('keeps ownershipHistory at 20 entries', async () => {
      const history = Array.from({ length: 20 }, () => ({ fromUserId: owner, toUserId: b, at: new Date(0) }))
      await ChatSession.updateOne({ _id: sid }, { $set: { ownershipHistory: history } })
      await xfer(b)
      const s = await load()
      expect(s.ownershipHistory).to.have.length(20)
      expect(String(s.ownershipHistory![19]!.toUserId)).to.equal(String(b))
      expect(s.ownershipHistory![19]!.at.getTime()).to.equal(clock.now())
    })

    it('clears the new owner and old owner from hiddenFor', async () => {
      await ChatSession.updateOne({ _id: sid }, { $set: { hiddenFor: [b, owner, d] } })
      await xfer(b)
      expect((await load()).hiddenFor!.map(String)).to.deep.equal([String(d)])
    })
  })

  describe('updateSettings', () => {
    it('sets only the given keys and bumps rev and aclVersion once', async () => {
      const res = await repo.updateSettings(scope(), { editorsCanInvite: true })
      expect(res).to.include({ status: 'applied', rev: 1, aclVersion: 1 })
      const s = await load()
      expect(s.settings).to.include({ editorsCanInvite: true, ownerContentShared: false })
      await repo.updateSettings(scope(), { ownerContentShared: true })
      expect((await load()).settings).to.include({ editorsCanInvite: true, ownerContentShared: true })
    })

    it('an empty patch is unchanged and writes nothing; another owner is not_found', async () => {
      expect(await repo.updateSettings(scope(), {})).to.deep.equal({ status: 'unchanged' })
      expect((await repo.updateSettings({ ...scope(), ownerId: String(b) }, { editorsCanInvite: true })).status).to.equal('not_found')
      expect((await load()).rev).to.equal(0)
    })
  })

  describe('leave', () => {
    it('pulls the direct row, hides the user, recomputes isShared, bumps once', async () => {
      await repo.add(scope(), addInput(b))
      await repo.add(scope(), addInput(c))
      const res = await repo.leave(anyScope(), String(b))
      expect(res).to.include({ status: 'applied', rev: 3, aclVersion: 3, isShared: true })
      const s = await load()
      expect(s.sharedWith!.map((r) => String(r.userId))).to.deep.equal([String(c)])
      expect(s.hiddenFor!.map(String)).to.deep.equal([String(b)])
    })

    it('hides a team-derived reader who has no direct row', async () => {
      await repo.add(scope(), { principal: { type: 'team', teamId: 'T1' }, accessLevel: 'read', addedBy: String(owner) })
      await repo.leave(anyScope(), String(b))
      const s = await load()
      expect(s.sharedWith).to.have.length(1)
      expect(s.hiddenFor!.map(String)).to.deep.equal([String(b)])
    })

    it('leaving twice keeps one hiddenFor entry', async () => {
      await repo.add(scope(), addInput(b))
      await repo.leave(anyScope(), String(b))
      await repo.leave(anyScope(), String(b))
      expect((await load()).hiddenFor).to.have.length(1)
    })

    it('the owner cannot leave at repository level', async () => {
      await repo.add(scope(), addInput(b))
      expect(await repo.leave(anyScope(), String(owner))).to.deep.equal({ status: 'owner' })
      const s = await load()
      expect(s.hiddenFor).to.have.length(0)
      expect(s.rev).to.equal(1)
    })

    it('not_found for an unknown session; re-add after leave un-hides (LC-22)', async () => {
      expect((await repo.leave({ ...anyScope(), sessionId: String(new Types.ObjectId()) }, String(b))).status).to.equal('not_found')
      await repo.add(scope(), addInput(b))
      await repo.leave(anyScope(), String(b))
      await repo.add(scope(), addInput(b))
      expect((await load()).hiddenFor).to.have.length(0)
    })
  })

  describe('soft-deleted chats', () => {
    it('every ACL mutation reports not_found and writes nothing', async () => {
      await repo.add(scope(), addInput(b, 'write'))
      await ChatSession.updateOne({ _id: sid }, { $set: { isDeleted: true } })
      const outcomes = [
        await repo.add(scope(), addInput(c)),
        await repo.changeLevel(scope(), user(b), 'read'),
        await repo.remove(scope(), user(b)),
        await repo.transfer(anyScope(), { fromUserId: String(owner), toUserId: String(b) }),
        await repo.updateSettings(scope(), { editorsCanInvite: true }),
        await repo.leave(anyScope(), String(b)),
      ].map((o) => o.status)
      expect(outcomes).to.deep.equal(['not_found', 'not_found', 'not_found', 'no_match', 'not_found', 'not_found'])
      const s = await load()
      expect(s.rev).to.equal(1)
      expect(s.sharedWith).to.have.length(1)
      expect(String(s.userId)).to.equal(String(owner))
      expect(s.hiddenFor).to.have.length(0)
      expect(s.settings?.editorsCanInvite).to.not.equal(true)
    })
  })

  describe('session option', function () {
    it('joins the caller transaction: rolled back on abort (replica set only)', async function () {
      const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
      if (!hello.setName) this.skip()
      const dbSession = await mongoose.startSession()
      try {
        await dbSession.withTransaction(async () => {
          await repo.add(scope(), addInput(b), { session: dbSession })
          await dbSession.abortTransaction()
        })
      } catch {
        // abort surfaces as a thrown error on some driver versions
      } finally {
        await dbSession.endSession()
      }
      const s = await load()
      expect(s.sharedWith).to.have.length(0)
      expect(s.rev).to.equal(0)
    })
  })
})
