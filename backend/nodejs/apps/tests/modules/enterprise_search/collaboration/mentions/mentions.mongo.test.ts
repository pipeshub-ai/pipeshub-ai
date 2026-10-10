import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { Types } from 'mongoose'
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { NoteService } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/note.service'
import { MentionValidator } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'
import { mentionedInFilter } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.queries'
import { MongoCollaboratorRepository } from '../../../../../src/modules/enterprise_search/services/collaboration/persistence/collaborator.repository'

const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('mentions against a real MongoDB (PR-10.4)', function () {
  this.timeout(60_000)
  const orgId = new Types.ObjectId()
  const otherOrg = new Types.ObjectId()
  const owner = new Types.ObjectId()
  const bob = new Types.ObjectId()
  const carol = new Types.ObjectId()
  let session: Types.ObjectId
  const previousRs = process.env.REPLICA_SET_AVAILABLE
  let replicaSet = false

  const validator = new MentionValidator({
    users: {
      displayNames: async () => new Map(),
      findByIds: async (_org, ids) => ids.map((userId) => ({ userId, displayName: userId, kind: 'human' as const, isDisabled: false })),
    },
    teams: { callerTeamIds: async () => ({ status: 'ok', teamIds: [] }), teamsVersion: async () => 0, exists: async () => true, memberUserIds: async () => ({ status: 'unresolved' }) },
    agents: { canExecute: async () => true, isServiceAccount: async () => false },
  })
  const service = new NoteService(validator)

  const grantFor = async (userId: Types.ObjectId) => {
    const doc = await ChatSession.findOne({ _id: session }).lean()
    const caller = { userId: String(userId), orgId: String(orgId), teamIds: [] as string[] }
    return { session: doc, role: 'write', via: [], caller, view: {} } as never
  }
  const identity = (userId: Types.ObjectId) => ({ userId: String(userId), orgId: String(orgId), authHeaders: {}, requestKey: {} })

  before(async () => {
    await mongoose.connect(uri as string)
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string }
    replicaSet = Boolean(hello.setName)
    await Promise.all([ChatSession.init(), ChatSessionMessage.init()])
  })

  beforeEach(async () => {
    process.env.REPLICA_SET_AVAILABLE = 'true'
    const created = await ChatSession.create({
      orgId,
      userId: owner,
      initiator: owner,
      title: 'real mongo',
      sessionType: 'chat',
      isShared: true,
      sharedWith: [
        { principalType: 'user', userId: bob, accessLevel: 'write' },
        { principalType: 'user', userId: carol, accessLevel: 'read' },
      ],
    })
    session = created._id as Types.ObjectId
  })

  afterEach(async () => {
    process.env.REPLICA_SET_AVAILABLE = previousRs
    await ChatSessionMessage.deleteMany({ orgId: { $in: [orgId, otherOrg] } })
    await ChatSession.deleteMany({ orgId })
  })

  after(async () => {
    await mongoose.disconnect()
  })

  it('declares the partial mentions index; the planner uses it for mentionedInFilter, sorted and without a SORT stage', async () => {
    const index = (await ChatSessionMessage.collection.indexes()).find((i) => JSON.stringify(i.key) === JSON.stringify({ orgId: 1, 'mentions.id': 1, createdAt: -1 }))
    expect(index?.partialFilterExpression).to.deep.equal({ 'mentions.id': { $type: 'string' } })
    const docs = Array.from({ length: 60 }, (_, i) => ({
      sessionId: session,
      orgId,
      seq: i + 1,
      messageType: i % 3 === 0 ? 'note' : 'user_query',
      content: 'x',
      authorUserId: owner,
      ...(i % 3 === 0 && { mentions: [{ type: 'user', id: String(carol) }] }),
    }))
    await ChatSessionMessage.insertMany(docs)
    type Plan = { queryPlanner: { winningPlan: unknown }; executionStats: { totalKeysExamined: number; nReturned: number } }
    const explain = async (filter: Record<string, unknown>) =>
      (await ChatSessionMessage.find(filter).sort({ createdAt: -1 }).explain('executionStats')) as unknown as Plan
    // A plain equality is not provably inside the partial filter, so it would scan the orgId index and sort.
    const plain = await explain({ orgId, 'mentions.id': String(carol) })
    expect(JSON.stringify(plain.queryPlanner.winningPlan)).to.not.contain('orgId_1_mentions.id_1_createdAt_-1')
    const plan = await explain(mentionedInFilter(orgId, String(carol)))
    expect(JSON.stringify(plan.queryPlanner.winningPlan)).to.contain('orgId_1_mentions.id_1_createdAt_-1')
    expect(JSON.stringify(plan.queryPlanner.winningPlan)).to.not.contain('"SORT"')
    expect(plan.executionStats.nReturned).to.equal(20)
    expect(plan.executionStats.totalKeysExamined, 'rows without mentions are not in the index').to.be.at.most(20)
  })

  it('a row without mentions has no mentions field at all (nothing changes for flag-off rows)', async () => {
    await ChatSessionMessage.create({ sessionId: session, orgId, seq: 1, messageType: 'user_query', content: 'plain', authorUserId: owner })
    const raw = await ChatSessionMessage.collection.findOne({ sessionId: session })
    expect(raw).to.not.have.property('mentions')
    const session1 = await ChatSession.collection.findOne({ _id: session })
    expect(session1?.settings ?? {}).to.not.have.property('respondMode')
  })

  it('the schema refuses more than 10 mentions and an unknown type', async () => {
    const many = Array.from({ length: 11 }, (_, i) => ({ type: 'user', id: `u${String(i)}` }))
    let error: Error | undefined
    await ChatSessionMessage.create({ sessionId: session, orgId, seq: 1, messageType: 'note', content: 'n', mentions: many }).catch((e: Error) => (error = e))
    expect(error?.message).to.match(/mentions exceeds 10/)
    error = undefined
    await ChatSessionMessage.create({ sessionId: session, orgId, seq: 2, messageType: 'note', content: 'n', mentions: [{ type: 'admin', id: 'x' }] }).catch((e: Error) => (error = e))
    expect(error).to.not.equal(undefined)
  })

  it('a note allocates seq and bumps rev in one transaction, takes no lease, and is idempotent under a race', async function () {
    if (!replicaSet) this.skip()
    const before = await ChatSession.findOne({ _id: session }).select('+nextSeq').lean<{ rev: number; nextSeq?: number }>()
    const grant = await grantFor(bob)
    const run = () => service.post(grant, identity(bob), 'chat', { query: 'for Carol', mentions: [{ type: 'user', id: String(carol) }], clientMessageId: 'same' })
    const results = await Promise.all([run(), run(), run()])
    expect(results.filter((r) => !r.duplicate)).to.have.length(1)
    expect(new Set(results.map((r) => r.note.id)).size).to.equal(1)
    const rows = await ChatSessionMessage.find({ sessionId: session }).lean()
    expect(rows).to.have.length(1)
    expect(rows[0]).to.include({ messageType: 'note', seq: 1 })
    expect(rows[0]!.mentions).to.deep.equal([{ type: 'user', id: String(carol) }])
    expect(String(rows[0]!.authorUserId)).to.equal(String(bob))
    const after = await ChatSession.findOne({ _id: session }).select('+nextSeq').lean<{ rev: number; nextSeq?: number; activeRun?: unknown }>()
    expect(after!.nextSeq).to.equal((before!.nextSeq ?? 0) + 1)
    expect(after!.rev).to.be.greaterThan(before!.rev ?? 0)
    expect(after!.activeRun ?? null).to.equal(null)
  })

  it('the real collaborator repository stores respondMode and the schema enum guards validated updates', async () => {
    const repo = new MongoCollaboratorRepository()
    const scope = { sessionId: String(session), orgId: String(orgId), ownerId: String(owner) }
    const out = await repo.updateSettings(scope, { respondMode: 'mention_only' })
    expect(out.status).to.equal('applied')
    const doc = await ChatSession.findOne({ _id: session }).lean<{ settings?: { respondMode?: string; editorsCanInvite?: boolean } }>()
    expect(doc?.settings?.respondMode).to.equal('mention_only')
    await ChatSession.collection.updateOne({ _id: session }, { $set: { 'settings.respondMode': 'loud' } })
    let rejected = false
    await ChatSession.findOneAndUpdate({ _id: session }, { $set: { 'settings.respondMode': 'loud' } }, { runValidators: true }).catch(() => (rejected = true))
    expect(rejected).to.equal(true)
  })
})
