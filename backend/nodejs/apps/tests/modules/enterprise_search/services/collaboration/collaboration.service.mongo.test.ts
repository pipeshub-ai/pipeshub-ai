import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { AuditEvent } from '../../../../../src/libs/audit/audit-event.schema'
import { MongoAuditWriter } from '../../../../../src/libs/audit/audit.writer'
import { ChatAccessLoader } from '../../../../../src/modules/authz/loaders/chat.loader'
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ChatSessionReadState } from '../../../../../src/modules/enterprise_search/schema/chat.session.read-state.schema'
import '../../../../../src/modules/enterprise_search/schema/citation.schema'
import { toAccessView } from '../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'
import { ConversationCollaborationService } from '../../../../../src/modules/enterprise_search/services/collaboration/conversation-collaboration.service'
import { ConversationAccessGrant } from '../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { MutationEffects } from '../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-effects'
import { MongoMutationRunner } from '../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-runner'
import { MongoCollaboratorRepository } from '../../../../../src/modules/enterprise_search/services/collaboration/persistence/collaborator.repository'
import { MongoConversationMessageFeed } from '../../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'
import { MongoReadStateRepository } from '../../../../../src/modules/enterprise_search/services/collaboration/persistence/read-state.repository'
import { PrincipalResolver } from '../../../../../src/modules/enterprise_search/services/collaboration/principals/principal-resolver'
import { CollaboratorListProjector } from '../../../../../src/modules/enterprise_search/services/collaboration/views/collaborator-list.projector'
import { LegacySharing } from '../../../../../src/modules/enterprise_search/services/collaboration/legacy/legacy-sharing'
import { RecordingNotifier } from '../../helpers/collaboration-world'

// A transaction needs a replica set; a standalone mongod runs only the ordered-write cases.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('collaboration service against a real MongoDB', function () {
  this.timeout(30_000)
  const orgId = new Types.ObjectId()
  const owner = new Types.ObjectId()
  const b = new Types.ObjectId()
  const c = new Types.ObjectId()
  const orgStr = String(orgId)
  let replicaSet = false

  const users = new Map([owner, b, c].map((id, i) => [String(id), `User ${i}`]))
  const directory = {
    displayNames: async (_o: string, ids: readonly string[]) => new Map(ids.map((id) => [id, users.get(id) ?? ''])),
    findByIds: async (_o: string, ids: readonly string[]) =>
      ids.filter((id) => users.has(id)).map((userId) => ({ userId, displayName: users.get(userId)!, kind: 'human' as const, isDisabled: false })),
  }
  const teams = { callerTeamIds: async () => ({ status: 'ok' as const, teamIds: [] }), teamsVersion: async () => 0, exists: async () => false }
  const flags = { isEnabled: async () => true }

  const build = (rs: boolean, notifier = new RecordingNotifier(), audit = new MongoAuditWriter()) => {
    const loader = new ChatAccessLoader()
    const service = new ConversationCollaborationService({
      repo: new MongoCollaboratorRepository(),
      effects: new MutationEffects(audit, notifier, { error: () => undefined }),
      runner: new MongoMutationRunner(rs),
      principals: new PrincipalResolver(directory, teams),
      projector: new CollaboratorListProjector(directory, teams, { describeMany: async () => new Map() }),
      emailIntents: { forDirectUsers: async () => new Map() },
      chats: loader,
      projects: { roleOf: async () => null, assertAtLeast: async () => ({}) as never, accessibleProjectIds: async () => [] },
      flags,
      legacy: new LegacySharing('http://iam', rs),
    })
    return { service, notifier, loader }
  }

  const identity = (userId: Types.ObjectId) => ({ userId: String(userId), orgId: orgStr, authHeaders: {}, requestKey: {} })
  const make = async (extra: Record<string, unknown> = {}): Promise<string> => String((await ChatSession.create({ orgId, userId: owner, initiator: owner, ...extra }))._id)
  const grantOf = async (sessionId: string, userId = owner): Promise<ConversationAccessGrant> => {
    const loaded = (await new ChatAccessLoader().loadScoped(orgStr, { id: sessionId, kind: 'chat' }))!
    const caller = { userId: String(userId), orgId: orgStr, teamIds: [] as string[] }
    const role = String(loaded.session.userId) === String(userId) ? 'owner' : 'write'
    return { session: loaded.session, role, via: [], caller, view: toAccessView(role, loaded.session, caller, { collab: true }) }
  }
  const add = (id: Types.ObjectId, accessLevel: 'read' | 'write' = 'read') => ({ principal: { type: 'user' as const, userId: String(id) }, accessLevel })
  const auditRows = (sessionId: string) => AuditEvent.find({ orgId, targetId: sessionId }).lean()

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    replicaSet = Boolean(hello.setName)
    await Promise.all([ChatSession.init(), AuditEvent.init(), ChatSessionReadState.init(), ChatSessionMessage.init()])
  })
  after(async () => {
    await Promise.all([ChatSession.deleteMany({ orgId }), AuditEvent.deleteMany({ orgId }), ChatSessionReadState.deleteMany({ orgId }), ChatSessionMessage.deleteMany({ orgId })])
    await mongoose.disconnect()
  })

  describe('replica set: session row, audit row and events share one transaction', () => {
    beforeEach(function () {
      if (!replicaSet) this.skip()
    })

    it('commits all three together and hands the notifier the transaction session', async () => {
      const sid = await make()
      const { service, notifier } = build(true)
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b, 'write')] })
      const stored = await ChatSession.findById(sid).lean()
      expect(stored!.sharedWith).to.have.length(1)
      expect(stored!.rev).to.equal(1)
      expect(await auditRows(sid)).to.have.length(1)
      expect(notifier.options[0]?.session, 'events are written in the transaction').to.not.equal(undefined)
    })

    it('a notifier failure rolls back the row and the audit row', async () => {
      const sid = await make()
      const { service, notifier } = build(true)
      notifier.failWith = new Error('outbox down')
      let error: Error | undefined
      try {
        await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b)] })
      } catch (e) {
        error = e as Error
      }
      expect(error?.message).to.equal('outbox down')
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(0)
      expect(await auditRows(sid)).to.have.length(0)
    })

    it('an audit failure rolls the row back too', async () => {
      const sid = await make()
      const { service } = build(true, new RecordingNotifier(), { record: async () => Promise.reject(new Error('audit down')) })
      let error: Error | undefined
      try {
        await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b)] })
      } catch (e) {
        error = e as Error
      }
      expect(error?.message).to.equal('audit down')
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(0)
    })

    it('a transfer is atomic with its audit row', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      const { service } = build(true)
      await service.transferOwnership(await grantOf(sid), identity(owner), String(b))
      const stored = await ChatSession.findById(sid).lean()
      expect(String(stored!.userId)).to.equal(String(b))
      expect((await auditRows(sid)).map((r) => r.action)).to.deep.equal(['chat.ownershipTransfer'])
    })
  })

  describe('ordered writes (standalone semantics, also valid on a replica set)', () => {
    it('a failing notifier leaves the share and its audit row in place', async () => {
      const sid = await make()
      const { service, notifier } = build(false)
      notifier.failWith = new Error('outbox down')
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b)] })
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(1)
      expect(await auditRows(sid)).to.have.length(1)
    })

    it('DB-04 style retry: repeating the same PUT writes one row and one audit row', async () => {
      const sid = await make()
      const { service } = build(false)
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b)] })
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [add(b)] })
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(1)
      expect(await auditRows(sid)).to.have.length(1)
    })

    it('two parallel PUTs for different people keep both rows (DB-01)', async () => {
      const sid = await make()
      const { service } = build(false)
      const g = await grantOf(sid)
      await Promise.all([
        service.upsert(g, identity(owner), { collaborators: [add(b)] }),
        service.upsert(g, identity(owner), { collaborators: [add(c)] }),
      ])
      const stored = await ChatSession.findById(sid).lean()
      expect(stored!.sharedWith).to.have.length(2)
      expect(stored!.isShared).to.equal(true)
      expect(stored!.aclVersion).to.equal(2)
      const { service: remover } = build(false)
      await remover.remove(g, identity(owner), { type: 'user', userId: String(b) })
      await remover.remove(g, identity(owner), { type: 'user', userId: String(c) })
      expect((await ChatSession.findById(sid).lean())!.isShared).to.equal(false)
    })

    it('DB-03: two transfers racing to different targets: one wins, the loser is told it is no longer the owner', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }, { principalType: 'user', userId: c, accessLevel: 'write' }] })
      const { service } = build(false)
      const g = await grantOf(sid)
      const results = await Promise.allSettled([service.transferOwnership(g, identity(owner), String(b)), service.transferOwnership(g, identity(owner), String(c))])
      const fulfilled = results.filter((r) => r.status === 'fulfilled')
      const rejected = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected')
      expect(fulfilled).to.have.length(1)
      expect(rejected).to.have.length(1)
      expect((rejected[0]!.reason as { code: string }).code).to.equal('CONVERSATION_OWNER_ONLY')
      expect((await ChatSession.findById(sid).lean())!.ownershipHistory).to.have.length(1)
      expect(await auditRows(sid)).to.have.length(1)
    })

    it('updateSettings and leave write through with their audit rows', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      const { service } = build(false)
      await service.updateSettings(await grantOf(sid), identity(owner), { editorsCanInvite: true })
      await service.leave(await grantOf(sid, b), identity(b))
      const stored = await ChatSession.findById(sid).select('+hiddenFor').lean()
      expect(stored!.settings!.editorsCanInvite).to.equal(true)
      expect(stored!.sharedWith).to.have.length(0)
      expect(stored!.hiddenFor!.map(String)).to.deep.equal([String(b)])
      expect((await auditRows(sid)).map((r) => r.action).sort()).to.deep.equal(['chat.leave', 'chat.settingsChange'])
    })

    it('legacy share with the flag on honours the level; unshare removes it', async () => {
      const sid = await make()
      const { service } = build(false)
      const shared = await service.legacyShare(await grantOf(sid), identity(owner), [String(b)], 'write')
      expect(shared.appliedAccessLevel).to.equal('write')
      expect(shared.isShared).to.equal(true)
      const unshared = await service.legacyUnshare(await grantOf(sid), identity(owner), [String(b)])
      expect(unshared.isShared).to.equal(false)
    })
  })

  describe('legacy /share and /unshare over rows written before PH-03 (data-shape tolerance)', () => {
    // Inserted raw so Mongoose adds no principalType, addedAt or default to the stored rows.
    const makeRaw = async (sharedWith: Record<string, unknown>[]): Promise<string> => {
      const _id = new Types.ObjectId()
      const now = new Date()
      await ChatSession.collection.insertOne({ _id, orgId, userId: owner, initiator: owner, sessionType: 'chat', isShared: true, sharedWith, isDeleted: false, aclVersion: 3, rev: 0, createdAt: now, updatedAt: now })
      return String(_id)
    }
    const legacyRows = () => [
      { _id: new Types.ObjectId(), userId: b, accessLevel: 'read' },
      { principalType: 'team', teamId: 't1', accessLevel: 'read' },
      { principalType: 'team', principalId: 't2', accessLevel: 'read' },
    ]
    const rowsOf = async (sid: string) => ((await ChatSession.collection.findOne({ _id: new Types.ObjectId(sid) })) as { sharedWith: Record<string, unknown>[]; isShared: boolean; aclVersion: number })

    it('share updates a row without principalType in place, adds the new user and keeps the team rows', async () => {
      const sid = await makeRaw(legacyRows())
      const { service } = build(false)
      const result = await service.legacyShare(await grantOf(sid), identity(owner), [String(b), String(c)], 'write')
      const stored = await rowsOf(sid)
      const userRows = stored.sharedWith.filter((r) => r.userId !== undefined)
      expect(userRows.filter((r) => String(r.userId) === String(b)), 'b is updated, not duplicated').to.have.length(1)
      expect(userRows.find((r) => String(r.userId) === String(b))!.accessLevel).to.equal('write')
      expect(userRows.find((r) => String(r.userId) === String(c))).to.deep.include({ principalType: 'user', accessLevel: 'write' })
      expect(stored.sharedWith.find((r) => r.teamId === 't1'), 'team row kept').to.deep.include({ accessLevel: 'read' })
      expect(stored.sharedWith.find((r) => r.principalId === 't2'), 'principalId-keyed team row kept').to.deep.include({ accessLevel: 'read' })
      expect(stored.sharedWith).to.have.length(4)
      expect(stored.aclVersion).to.be.greaterThan(3)
      expect(result.isShared).to.equal(true)
      expect(result.sharedWith).to.have.length(4)
    })

    it('unshare removes a row without principalType and leaves the rest', async () => {
      const sid = await makeRaw(legacyRows())
      const { service } = build(false)
      const result = await service.legacyUnshare(await grantOf(sid), identity(owner), [String(b)])
      const stored = await rowsOf(sid)
      expect(stored.sharedWith.some((r) => String(r.userId) === String(b))).to.equal(false)
      expect(stored.sharedWith.find((r) => r.teamId === 't1')).to.not.equal(undefined)
      expect(stored.sharedWith.find((r) => r.principalId === 't2')).to.not.equal(undefined)
      expect(stored.isShared).to.equal(true)
      expect(stored.aclVersion).to.be.greaterThan(3)
      expect(result.isShared).to.equal(true)
    })
  })

  describe('feed reads and read state', () => {
    const feed = new MongoConversationMessageFeed()
    const reads = new MongoReadStateRepository()

    it('readRev projects the revision only; readHead reports the head; listAfter pages by seq', async () => {
      const sid = await make({ rev: 4, nextSeq: 3, lastActivityAt: 123, activeRun: { runId: 'r', userId: b, startedAt: new Date(0), leaseExpiresAt: new Date(1) } })
      for (const seq of [1, 2, 3]) await ChatSessionMessage.create({ sessionId: sid, orgId, seq, messageType: 'user_query', content: `m${seq}` })
      expect(await feed.readRev(sid, orgStr)).to.equal(4)
      expect(await feed.readRev(String(new Types.ObjectId()), orgStr)).to.equal(null)
      expect(await feed.readRev(sid, String(new Types.ObjectId()))).to.equal(null)
      const head = await feed.readHead(sid, orgStr)
      expect(head).to.deep.include({ rev: 4, lastSeq: 3, lastActivityAt: 123 })
      expect(head!.activeRun).to.deep.include({ runId: 'r', userId: String(b) })
      expect((await feed.listAfter(sid, 1, 10)).map((m) => m.content)).to.deep.equal(['m2', 'm3'])
      expect(await feed.listAfter(sid, 1, 1)).to.have.length(1)
    })

    it('markRead with an interval writes the first time and then leaves a recent row alone, without error', async () => {
      const sid = String(new Types.ObjectId())
      await reads.markRead(orgStr, String(b), sid, 3, { minIntervalMs: 60_000 })
      await reads.markRead(orgStr, String(b), sid, 9, { minIntervalMs: 60_000 })
      expect((await reads.lastReadSeqs(String(b), [sid])).get(sid)).to.equal(3)
      await reads.markRead(orgStr, String(b), sid, 9)
      expect((await reads.lastReadSeqs(String(b), [sid])).get(sid)).to.equal(9)
      await reads.markRead(orgStr, String(b), sid, 2)
      expect((await reads.lastReadSeqs(String(b), [sid])).get(sid)).to.equal(9)
    })

    it('lastReadSeqs answers one page in one query and skips sessions never opened', async () => {
      const opened = String(new Types.ObjectId())
      const unopened = String(new Types.ObjectId())
      await reads.markRead(orgStr, String(c), opened, 5)
      const map = await reads.lastReadSeqs(String(c), [opened, unopened])
      expect([...map.entries()]).to.deep.equal([[opened, 5]])
      expect((await reads.lastReadSeqs(String(c), [])).size).to.equal(0)
    })
  })
})
