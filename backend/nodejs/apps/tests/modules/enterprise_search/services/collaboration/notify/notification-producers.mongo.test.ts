import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose, { Types } from 'mongoose'
import { AuditEvent } from '../../../../../../src/libs/audit/audit-event.schema'
import { MongoAuditWriter } from '../../../../../../src/libs/audit/audit.writer'
import { OutboxEvent } from '../../../../../../src/libs/services/outbox/outbox.schema'
import { OutboxDispatcher } from '../../../../../../src/libs/services/outbox/outbox.dispatcher'
import { ChatAccessLoader } from '../../../../../../src/modules/authz/loaders/chat.loader'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ChatSessionReadState } from '../../../../../../src/modules/enterprise_search/schema/chat.session.read-state.schema'
import '../../../../../../src/modules/enterprise_search/schema/citation.schema'
import { toAccessView } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'
import { ConversationCollaborationService } from '../../../../../../src/modules/enterprise_search/services/collaboration/conversation-collaboration.service'
import { ConversationAccessGrant } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { LegacySharing } from '../../../../../../src/modules/enterprise_search/services/collaboration/legacy/legacy-sharing'
import { MutationEffects } from '../../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-effects'
import { MongoMutationRunner } from '../../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-runner'
import { OutboxCollaborationNotifier } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-notifier'
import { ConversationEventProducers } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { MongoNotificationArchiver } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/notification-archiver'
import { NotificationOutboxWriter } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/notification-outbox.writer'
import { RecipientResolver } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/recipient-resolver'
import { MongoChatAudienceRepository } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/chat-audience.repository'
import { MongoCollaboratorRepository } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/collaborator.repository'
import { MongoReadStateRepository } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/read-state.repository'
import { PrincipalResolver } from '../../../../../../src/modules/enterprise_search/services/collaboration/principals/principal-resolver'
import { CollaboratorListProjector } from '../../../../../../src/modules/enterprise_search/services/collaboration/views/collaborator-list.projector'
import { Notifications } from '../../../../../../src/modules/notification/schema/notification.schema'
import { UserNotificationPreferences } from '../../../../../../src/modules/notification/schema/user-notification-preferences.schema'
import { MongoNotificationPreferencesRepository } from '../../../../../../src/modules/notification/repository/notification-preferences.repository'
import { NotificationConsumer } from '../../../../../../src/modules/notification/service/notification.consumer'
import { NotificationService } from '../../../../../../src/modules/notification/service/notification.service'

// Transactions need a replica set; a standalone mongod runs only the ordered-write cases.
const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('collaboration notifications against a real MongoDB: service, outbox, dispatcher, consumer', function () {
  this.timeout(60_000)
  const orgId = new Types.ObjectId()
  const orgStr = String(orgId)
  const [owner, b, c, d, e, f] = Array.from({ length: 6 }, () => new Types.ObjectId())
  const ids = [owner, b, c, d, e, f].map(String)
  const known = new Set(ids)
  let replicaSet = false

  const TEAMS: Record<string, string[] | 'too-large' | 'down'> = { T: [String(c), String(d), String(owner)], BIG: 'too-large', DOWN: 'down' }
  const directory = {
    displayNames: async (_o: string, list: readonly string[]) => new Map(list.map((id) => [id, known.has(id) ? 'Someone' : ''])),
    findByIds: async (_o: string, list: readonly string[]) =>
      list.filter((id) => known.has(id)).map((userId) => ({ userId, displayName: 'Someone', kind: 'human' as const, isDisabled: false })),
  }
  const teams = {
    callerTeamIds: async () => ({ status: 'ok' as const, teamIds: Object.keys(TEAMS) }),
    teamsVersion: async () => 0,
    exists: async (teamId: string) => teamId in TEAMS,
    memberUserIds: async (teamId: string) => {
      const members = TEAMS[teamId]
      if (members === 'down') throw new Error('connector down')
      return members === undefined || members === 'too-large' ? { status: 'unresolved' as const } : { status: 'ok' as const, userIds: members }
    },
  }
  const emailIntents = {
    forDirectUsers: async (args: { template: never; recipients: ReadonlyArray<{ userId: string; accessLevel: 'read' | 'write' }> }) =>
      new Map(args.recipients.map((r) => [r.userId, { template: args.template, actorName: 'Alice', orgName: 'Acme', accessLevel: r.accessLevel }])),
  }
  const quiet = { warn: () => undefined, error: () => undefined }

  const notifierFor = () =>
    new OutboxCollaborationNotifier({
      recipients: new RecipientResolver(directory as never, teams as never, quiet as never, 5000),
      outbox: new NotificationOutboxWriter(),
      archiver: new MongoNotificationArchiver(),
    })

  const build = (rs: boolean, notifier = notifierFor()) => {
    const loader = new ChatAccessLoader()
    return new ConversationCollaborationService({
      repo: new MongoCollaboratorRepository(),
      effects: new MutationEffects(new MongoAuditWriter(), notifier, quiet as never),
      runner: new MongoMutationRunner(rs),
      principals: new PrincipalResolver(directory as never, teams as never),
      projector: new CollaboratorListProjector(directory as never, teams as never, { describeMany: async () => new Map() }),
      emailIntents: emailIntents as never,
      chats: loader,
      projects: { roleOf: async () => null, assertAtLeast: async () => ({}) as never, accessibleProjectIds: async () => [] },
      flags: { isEnabled: async () => true },
      legacy: new LegacySharing('http://iam', rs),
    })
  }

  const identity = (userId: Types.ObjectId) => ({ userId: String(userId), orgId: orgStr, authHeaders: {}, requestKey: {} })
  const sessions: string[] = []
  const make = async (extra: Record<string, unknown> = {}): Promise<string> => {
    const sid = String((await ChatSession.create({ orgId, userId: owner, initiator: owner, ...extra }))._id)
    sessions.push(sid)
    return sid
  }
  const grantOf = async (sessionId: string, userId = owner): Promise<ConversationAccessGrant> => {
    const loaded = (await new ChatAccessLoader().loadScoped(orgStr, { id: sessionId, kind: 'chat' }))!
    const caller = { userId: String(userId), orgId: orgStr, teamIds: [] as string[] }
    const role = String(loaded.session.userId) === String(userId) ? 'owner' : 'write'
    return { session: loaded.session, role, via: [], caller, view: toAccessView(role, loaded.session, caller, { collab: true }) }
  }
  const user = (id: Types.ObjectId, accessLevel: 'read' | 'write' = 'read') => ({ principal: { type: 'user' as const, userId: String(id) }, accessLevel })
  const team = (teamId: string, accessLevel: 'read' | 'write' = 'read') => ({ principal: { type: 'team' as const, teamId }, accessLevel })
  const outboxRows = (sid: string) => OutboxEvent.find({ orderingKey: `chat:${sid}` }).sort({ createdAt: 1, _id: 1 }).lean()

  // The consumer, fed by the real dispatcher.
  let sendToUser: sinon.SinonStub
  let dispatchEmail: sinon.SinonStub
  let deliver: (value: unknown) => Promise<void>
  const pump = async (): Promise<number> => {
    const producer = { isConnected: () => true, connect: async () => undefined, publish: async (_topic: string, message: { value: string }) => deliver(message.value) }
    const dispatcher = new OutboxDispatcher(producer as never, { info: () => undefined, warn: () => undefined, error: () => undefined } as never)
    return dispatcher.drain()
  }
  const docsFor = (userId: Types.ObjectId, type?: string) => Notifications.find({ orgId, assignedTo: userId, ...(type && { type }) }).sort({ createdAt: 1, _id: 1 }).lean()

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    replicaSet = Boolean(hello.setName)
    await Promise.all([ChatSession.init(), AuditEvent.init(), ChatSessionReadState.init(), ChatSessionMessage.init(), Notifications.init(), OutboxEvent.init(), UserNotificationPreferences.init()])
  })
  after(async () => {
    await Promise.all([
      ChatSession.deleteMany({ orgId }),
      AuditEvent.deleteMany({ orgId }),
      ChatSessionReadState.deleteMany({ orgId }),
      ChatSessionMessage.deleteMany({ orgId }),
      Notifications.deleteMany({ orgId }),
      UserNotificationPreferences.deleteMany({ orgId }),
      OutboxEvent.deleteMany({ orderingKey: { $in: sessions.map((s) => `chat:${s}`) } }),
    ])
    await mongoose.disconnect()
  })
  beforeEach(async () => {
    sendToUser = sinon.stub()
    dispatchEmail = sinon.stub().resolves()
    const broker = { isConnected: () => true, consume: sinon.stub().resolves() }
    const service = sinon.createStubInstance(NotificationService)
    ;(service.sendToUser as unknown as sinon.SinonStub) = sendToUser
    const consumer = new NotificationConsumer(broker as never, { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() } as never, service as never, { dispatch: dispatchEmail })
    await consumer.consume(async () => undefined)
    const wrapped = broker.consume.firstCall.args[0]
    deliver = (value) => wrapped({ value })
  })
  afterEach(async () => {
    sinon.restore()
    await Promise.all([Notifications.deleteMany({ orgId }), ChatSessionReadState.deleteMany({ orgId }), UserNotificationPreferences.deleteMany({ orgId }), ChatSessionMessage.deleteMany({ orgId })])
    await OutboxEvent.deleteMany({ orderingKey: { $in: sessions.map((s) => `chat:${s}`) } })
  })

  describe('replica set: outbox rows share the share\'s transaction', () => {
    beforeEach(function () {
      if (!replicaSet) this.skip()
    })

    it('commits the session row, the audit row and the outbox row together', async () => {
      const sid = await make()
      await build(true).upsert(await grantOf(sid), identity(owner), { collaborators: [user(b, 'write')] })
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(1)
      const rows = await outboxRows(sid)
      expect(rows).to.have.length(1)
      expect(rows[0]).to.deep.include({ topic: 'notification', key: 'chat.shared', status: 'pending' })
      const value = JSON.parse(rows[0]!.value)
      expect(value).to.deep.include({ orgId: orgStr, type: 'chat.shared', recipientUserIds: [String(b)], dedupeKey: `chat.shared:${sid}:1` })
      expect(value.payload).to.deep.equal({ sessionId: sid, kind: 'chat', actorUserId: String(owner), accessLevel: 'write' })
    })

    it('rolls the outbox rows back with a transaction that fails after the write', async () => {
      const sid = await make()
      const notifier = notifierFor()
      let error: Error | undefined
      try {
        await new MongoMutationRunner(true).run(async (session) => {
          await notifier.publish(
            [{ type: 'chat.deleted', orgId: orgStr, sessionId: sid, ref: { kind: 'chat', conversationId: sid }, actorUserId: String(owner), recipientUserIds: [String(b)] }],
            { session, identity: identity(owner) },
          )
          expect(await OutboxEvent.countDocuments({ orderingKey: `chat:${sid}` }).session(session!)).to.equal(1)
          throw new Error('later step failed')
        })
      } catch (err) {
        error = err as Error
      }
      expect(error?.message).to.equal('later step failed')
      expect(await outboxRows(sid)).to.have.length(0)
    })

    it('a failing outbox write rolls back the share and its audit row', async () => {
      const sid = await make()
      sinon.stub(OutboxEvent, 'create').rejects(new Error('outbox down'))
      let error: Error | undefined
      try {
        await build(true).upsert(await grantOf(sid), identity(owner), { collaborators: [user(b)] })
      } catch (err) {
        error = err as Error
      }
      expect(error?.message).to.equal('outbox down')
      expect((await ChatSession.findById(sid).lean())!.sharedWith).to.have.length(0)
      expect(await AuditEvent.countDocuments({ orgId, targetId: sid })).to.equal(0)
    })

    it('a transfer writes its two rows in the transaction', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      await build(true).transferOwnership(await grantOf(sid), identity(owner), String(b))
      const rows = (await outboxRows(sid)).map((r) => JSON.parse(r.value))
      expect(rows.map((r) => r.recipientUserIds)).to.deep.equal([[String(b)], [String(owner)]])
      expect(rows.map((r) => r.emailIntent !== undefined)).to.deep.equal([true, false])
    })
  })

  describe('delivery', () => {
    for (const rs of [true, false]) {
      it(`${rs ? 'replica set' : 'standalone'}: a share reaches the recipient's bell once, and a redelivered row adds nothing`, async function () {
        if (rs && !replicaSet) this.skip()
        const sid = await make()
        await build(rs).upsert(await grantOf(sid), identity(owner), { collaborators: [user(b, 'write')] })
        expect(await pump()).to.equal(1)
        const docs = await docsFor(b)
        expect(docs).to.have.length(1)
        expect(docs[0]).to.deep.include({ type: 'chat.shared', status: 'unread', redirectLink: `/chat?conversationId=${sid}` })
        expect(docs[0]!.payload).to.deep.include({ sessionId: sid, accessLevel: 'write' })
        expect(docs[0]).to.not.have.property('emailIntent')
        expect(sendToUser.callCount).to.equal(1)

        const row = (await outboxRows(sid))[0]!
        await deliver(row.value)
        expect(await docsFor(b)).to.have.length(1)
        expect(sendToUser.callCount).to.equal(1)
        expect(await docsFor(owner)).to.have.length(0)
      })
    }

    it('an email intent reaches the consumer once, for the new doc only', async () => {
      const sid = await make()
      await build(false).upsert(await grantOf(sid), identity(owner), { collaborators: [user(b)] })
      await pump()
      expect(dispatchEmail.callCount).to.equal(1)
      expect(dispatchEmail.firstCall.args[0]).to.deep.include({ dedupeKey: `chat.shared:${sid}:1`, redirectLink: `/chat?conversationId=${sid}` })
      expect(dispatchEmail.firstCall.args[0].emailIntent).to.deep.equal({ template: 'chatShared', actorName: 'Alice', orgName: 'Acme', accessLevel: 'read' })
      await deliver((await outboxRows(sid))[0]!.value)
      expect(dispatchEmail.callCount).to.equal(1)
    })

    it('unshare then re-share notifies again; the first notification is archived by the unshare', async () => {
      const sid = await make()
      const service = build(false)
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [user(b)] })
      await pump()
      await service.remove(await grantOf(sid), identity(owner), { type: 'user', userId: String(b) })
      expect((await outboxRows(sid)).filter((r) => r.key === 'chat.unshared')).to.have.length(0)
      expect((await docsFor(b, 'chat.shared')).map((n) => n.status)).to.deep.equal(['archived'])
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [user(b)] })
      await pump()
      const docs = await docsFor(b, 'chat.shared')
      expect(docs.map((n) => [n.status, n.dedupeKey])).to.deep.equal([
        ['archived', `chat.shared:${sid}:1`],
        ['unread', `chat.shared:${sid}:3`],
      ])
      expect(sendToUser.callCount).to.equal(2)
    })

    it('NT-03: removing someone leaves only that user\'s earlier share archived and writes no outbox row', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'read' }, { principalType: 'user', userId: c, accessLevel: 'read' }] })
      const other = await make()
      await Notifications.create([b, c].map((u) => ({ orgId, assignedTo: u, type: 'chat.shared', payload: { sessionId: sid }, dedupeKey: `x:${String(u)}` })))
      await Notifications.create({ orgId, assignedTo: b, type: 'chat.shared', payload: { sessionId: other }, dedupeKey: 'other' })
      await build(false).remove(await grantOf(sid), identity(owner), { type: 'user', userId: String(b) })
      expect(await outboxRows(sid)).to.have.length(0)
      const states = async (u: Types.ObjectId) => (await docsFor(u)).map((n) => [(n.payload as { sessionId: string }).sessionId === sid, n.status])
      expect(await states(b)).to.deep.equal([[true, 'archived'], [false, 'unread']])
      expect(await states(c)).to.deep.equal([[true, 'unread']])
    })

    it('leaving archives only the leaver\'s earlier share notification and writes no outbox row', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }, { principalType: 'user', userId: c, accessLevel: 'read' }] })
      await Notifications.create([b, c].map((u) => ({ orgId, assignedTo: u, type: 'chat.shared', payload: { sessionId: sid }, dedupeKey: `leave:${String(u)}` })))
      await build(false).leave(await grantOf(sid, b), identity(b))
      expect(await outboxRows(sid)).to.have.length(0)
      expect((await docsFor(b, 'chat.shared')).map((n) => n.status)).to.deep.equal(['archived'])
      expect((await docsFor(c, 'chat.shared')).map((n) => n.status)).to.deep.equal(['unread'])
    })

    it('team expansion: B, team T (C, D, the owner), a team too large and a team that is down: the share still succeeds and reaches B, C, D', async () => {
      const sid = await make()
      const out = await build(false).upsert(await grantOf(sid), identity(owner), { collaborators: [user(b), team('T'), team('BIG'), team('DOWN')] })
      expect(out.collaborators).to.have.length(4)
      await pump()
      for (const u of [b, c, d]) expect(await docsFor(u, 'chat.shared'), String(u)).to.have.length(1)
      expect(await docsFor(owner)).to.have.length(0)
      const rows = (await outboxRows(sid)).map((r) => JSON.parse(r.value).recipientUserIds)
      // Both rows can carry the same createdAt millisecond, so their order is not defined.
      expect(rows).to.have.deep.members([[String(b)], [String(c), String(d)]])
    })

    it('an access change and a transfer are delivered with their own keys', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'read' }] })
      const service = build(false)
      await service.upsert(await grantOf(sid), identity(owner), { collaborators: [user(b, 'write')] })
      await service.transferOwnership(await grantOf(sid), identity(owner), String(b))
      await pump()
      expect((await docsFor(b)).map((n) => n.type)).to.deep.equal(['chat.accessChanged', 'chat.ownershipTransferred'])
      expect((await docsFor(owner)).map((n) => n.type)).to.deep.equal(['chat.ownershipTransferred'])
      expect(dispatchEmail.callCount).to.equal(1)
    })
  })

  describe('chat.deleted', () => {
    it('reaches users who authored a user_query, not the deleter or a viewer', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }, { principalType: 'user', userId: c, accessLevel: 'write' }, { principalType: 'user', userId: d, accessLevel: 'read' }] })
      const authors = [owner, b, c, b]
      for (const [i, authorUserId] of authors.entries()) await ChatSessionMessage.create({ sessionId: sid, orgId, seq: i + 1, messageType: 'user_query', content: 'q', authorUserId })
      await ChatSessionMessage.create({ sessionId: sid, orgId, seq: 9, messageType: 'bot_response', content: 'a', requestedBy: d })
      const producers = new ConversationEventProducers({
        audience: new MongoChatAudienceRepository(),
        readState: new MongoReadStateRepository(),
        preferences: new MongoNotificationPreferencesRepository(),
        notifier: notifierFor(),
        effects: new MutationEffects(new MongoAuditWriter(), notifierFor(), quiet as never),
      })
      await producers.conversationDeleted(await grantOf(sid))
      await pump()
      expect(await docsFor(b, 'chat.deleted')).to.have.length(1)
      expect(await docsFor(c, 'chat.deleted')).to.have.length(1)
      expect(await docsFor(d)).to.have.length(0)
      expect(await docsFor(owner)).to.have.length(0)
    })

    it('in a transaction the row rolls back with the delete', async function () {
      if (!replicaSet) this.skip()
      const sid = await make()
      await ChatSessionMessage.create({ sessionId: sid, orgId, seq: 1, messageType: 'user_query', content: 'q', authorUserId: b })
      const producers = new ConversationEventProducers({
        audience: new MongoChatAudienceRepository(),
        readState: new MongoReadStateRepository(),
        preferences: new MongoNotificationPreferencesRepository(),
        notifier: notifierFor(),
        effects: new MutationEffects(new MongoAuditWriter(), notifierFor(), quiet as never),
      })
      const grant = await grantOf(sid)
      try {
        await new MongoMutationRunner(true).run(async (session) => {
          await producers.conversationDeleted(grant, session)
          throw new Error('delete failed later')
        })
      } catch {
        // expected
      }
      expect(await outboxRows(sid)).to.have.length(0)
    })
  })

  describe('chat.activity', () => {
    const producersFor = () =>
      new ConversationEventProducers({
        audience: new MongoChatAudienceRepository(),
        readState: new MongoReadStateRepository(),
        preferences: new MongoNotificationPreferencesRepository(),
        notifier: notifierFor(),
        effects: new MutationEffects(new MongoAuditWriter(), notifierFor(), quiet as never),
      })
    const readAt = async (sid: string, u: Types.ObjectId, msAgo: number) => {
      await new MongoReadStateRepository().markRead(orgStr, String(u), sid, 1)
      await ChatSessionReadState.collection.updateOne({ userId: u, sessionId: new Types.ObjectId(sid) }, { $set: { updatedAt: new Date(Date.now() - msAgo) } })
    }

    it('NT-06: owner A read 5 min ago, C read 30 s ago, D muted, E has chat activity off, F is a reader: B\'s turn tells only A', async () => {
      const [a, writer, quickReader, muted, optedOut, viewer] = [owner, b, c, d, e, f]
      const sid = await make({
        isShared: true,
        sharedWith: [writer, quickReader, muted, optedOut].map((u) => ({ principalType: 'user', userId: u, accessLevel: 'write' })).concat([{ principalType: 'user', userId: viewer, accessLevel: 'read' }]),
      })
      await readAt(sid, a, 5 * 60_000)
      await readAt(sid, quickReader, 30_000)
      await new MongoNotificationPreferencesRepository().muteSession(orgStr, String(muted), sid)
      await new MongoNotificationPreferencesRepository().update(orgStr, String(optedOut), { inApp: { chatActivity: false } })
      await producersFor().turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(writer) })
      await pump()
      expect(await docsFor(a, 'chat.activity')).to.have.length(1)
      for (const u of [writer, quickReader, muted, optedOut, viewer]) expect(await docsFor(u), String(u)).to.have.length(0)
      const rows = await outboxRows(sid)
      expect(rows).to.have.length(1)
      expect(JSON.parse(rows[0]!.value)).to.deep.include({ coalesceKey: `chat.activity:${sid}`, recipientUserIds: [String(a)] })
      expect(JSON.parse(rows[0]!.value)).to.not.have.property('dedupeKey')
    })

    it('a user with no read state at all is told', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      await producersFor().turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(owner) })
      await pump()
      expect(await docsFor(b, 'chat.activity')).to.have.length(1)
    })

    it('two turns coalesce into one unread doc with count 2; reading it starts a new one', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      const producers = producersFor()
      await producers.turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(owner) })
      await producers.turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(owner) })
      await pump()
      let docs = await docsFor(b, 'chat.activity')
      expect(docs).to.have.length(1)
      expect((docs[0]!.payload as { count: number }).count).to.equal(2)
      await Notifications.updateOne({ _id: docs[0]!._id }, { $set: { status: 'read' } })
      await producers.turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(owner) })
      await pump()
      docs = await docsFor(b, 'chat.activity')
      expect(docs.map((n) => [n.status, (n.payload as { count: number }).count])).to.deep.equal([['read', 2], ['unread', 1]])
    })

    it('a deleted conversation or a session nobody else is in produces nothing', async () => {
      const alone = await make()
      const gone = await make({ isShared: true, isDeleted: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      await producersFor().turnEnded({ orgId: orgStr, sessionId: alone, senderUserId: String(owner) })
      await producersFor().turnEnded({ orgId: orgStr, sessionId: gone, senderUserId: String(owner) })
      expect(await outboxRows(alone)).to.have.length(0)
      expect(await outboxRows(gone)).to.have.length(0)
    })

    it('another org\'s id finds no audience for the session', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      const repo = new MongoChatAudienceRepository()
      expect(await repo.activityAudience(String(new Types.ObjectId()), sid)).to.equal(null)
      expect((await repo.activityAudience(orgStr, sid))?.userIds).to.have.members([String(owner), String(b)])
    })

    it('an outbox failure is swallowed so the turn is unaffected', async () => {
      const sid = await make({ isShared: true, sharedWith: [{ principalType: 'user', userId: b, accessLevel: 'write' }] })
      sinon.stub(OutboxEvent, 'create').rejects(new Error('outbox down'))
      await producersFor().turnEnded({ orgId: orgStr, sessionId: sid, senderUserId: String(owner) })
    })
  })

  describe('stores', () => {
    it('readSince matches only rows at or after the cut-off', async () => {
      const sid = await make()
      const reads = new MongoReadStateRepository()
      await reads.markRead(orgStr, String(b), sid, 1)
      await reads.markRead(orgStr, String(c), sid, 1)
      await ChatSessionReadState.collection.updateOne({ userId: c, sessionId: new Types.ObjectId(sid) }, { $set: { updatedAt: new Date(Date.now() - 600_000) } })
      const recent = await reads.readSince(sid, [String(b), String(c), String(d)], new Date(Date.now() - 120_000))
      expect([...recent]).to.deep.equal([String(b)])
      expect((await reads.readSince(sid, [], new Date(0))).size).to.equal(0)
    })

    it('getMany answers one query with defaults for users without a row', async () => {
      const prefs = new MongoNotificationPreferencesRepository()
      await prefs.update(orgStr, String(b), { inApp: { chatActivity: false } })
      await prefs.muteSession(orgStr, String(c), 'a'.repeat(24).replace(/a/g, '1'))
      const map = await prefs.getMany(orgStr, [String(b), String(c), String(d)])
      expect(map.get(String(b))!.inApp.chatActivity).to.equal(false)
      expect(map.get(String(c))!.mutedSessions).to.deep.equal(['1'.repeat(24)])
      expect(map.get(String(d))).to.deep.equal({
        email: { chatShared: true, ownershipTransferred: true, chatMentioned: false },
        inApp: { chatActivity: true, chatMentioned: true },
        mutedSessions: [],
        tipsSeen: [],
      })
      expect((await prefs.getMany(orgStr, [])).size).to.equal(0)
    })
  })
})
