/**
 * Runs the fork against a real MongoDB. Skipped unless FORK_TEST_MONGO_URI is
 * set; the URI needs a replica set to cover the transactional path, and
 * REPLICA_SET_AVAILABLE must match it. Each run uses its own database and
 * drops it afterwards.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import {
  copySessionUpToMessage,
  FORK_COPY_BATCH_SIZE,
} from '../../../../src/modules/enterprise_search/utils/fork-conversation'
import { forkConversation, forkAgentConversation, deleteConversationById } from '../../../../src/modules/enterprise_search/controller/es_controller'

const APP_CONFIG = { connectorBackend: 'http://connectors.test' } as never
const URI = process.env.FORK_TEST_MONGO_URI
const useTransactions = process.env.REPLICA_SET_AVAILABLE === 'true'
const oid = () => new mongoose.Types.ObjectId()

const describeIfMongo = URI ? describe : describe.skip

describeIfMongo(`fork conversation on a real MongoDB (transactions: ${useTransactions})`, function () {
  this.timeout(60000)
  const orgId = oid()
  const owner = oid()
  const stranger = oid()

  before(async () => {
    await mongoose.connect(URI as string, { dbName: `fork_test_${Date.now()}` })
    await ChatSession.syncIndexes()
    await ChatSessionMessage.syncIndexes()
  })

  after(async () => {
    await mongoose.connection.dropDatabase()
    await mongoose.disconnect()
  })

  afterEach(() => sinon.restore())

  const seedChat = async (count: number, extra: Record<string, unknown> = {}) => {
    const source = await ChatSession.create({
      sessionType: 'chat',
      userId: owner,
      initiator: owner,
      orgId,
      title: 'Quarterly numbers',
      ...extra,
    })
    const citationId = (
      await Citation.create({
        content: 'Q3 sales were 4M.',
        chunkIndex: 0,
        citationType: 'x',
        metadata: { origin: 'UPLOAD', recordName: 'q3.pdf', recordId: 'rec-1', mimeType: 'application/pdf', orgId: String(orgId) },
      })
    )._id
    const rows = Array.from({ length: count }, (_, i) => ({
      messageType: i % 2 === 0 ? 'user_query' : 'bot_response',
      content: `message ${i + 1}`,
      seq: i + 1,
      sessionId: source._id,
      orgId,
      ...(i % 2 === 1
        ? {
            citations: [{ citationId, relevanceScore: 0.9 }],
            feedback: [{ isHelpful: true }],
            parts: [{ type: 'tool', toolName: 'run_sql', artifactId: `artifact-${i + 1}` }],
            referenceData: [{ name: 'Q3', id: `ref-${i + 1}`, type: 'record' }],
          }
        : { attachments: [{ recordId: `rec-att-${i + 1}`, recordName: 'q3.pdf', mimeType: 'application/pdf', source: 'upload' }] }),
    }))
    const inserted = await ChatSessionMessage.insertMany(rows, { ordered: true })
    await ChatSession.updateOne({ _id: source._id }, { $set: { nextSeq: count } })
    const fresh = await ChatSession.findById(source._id)
    return { source: fresh as NonNullable<typeof fresh>, messages: inserted, citationId }
  }

  const fork = async (
    source: Awaited<ReturnType<typeof seedChat>>['source'],
    messageId: string,
    fields: Parameters<typeof copySessionUpToMessage>[4] = { sessionType: 'chat' },
  ) => {
    if (!useTransactions) {
      return copySessionUpToMessage(source, messageId, String(owner), String(orgId), fields, null)
    }
    const session = await mongoose.startSession()
    try {
      return await session.withTransaction(() =>
        copySessionUpToMessage(source, messageId, String(owner), String(orgId), fields, session),
      ) as Awaited<ReturnType<typeof copySessionUpToMessage>>
    } finally {
      await session.endSession()
    }
  }

  it('copies a long thread in order across batches, with fresh contiguous seq and no feedback', async () => {
    const total = FORK_COPY_BATCH_SIZE * 2 + 50
    const { source, messages, citationId } = await seedChat(total)

    const result = await fork(source, String(messages[total - 1]!._id))

    expect(result.messageCount).to.equal(total)
    const copied = await ChatSessionMessage.find({ sessionId: result.conversation._id }).sort({ seq: 1 }).lean()
    expect(copied).to.have.length(total)
    expect(copied.map((m) => m.seq)).to.deep.equal(Array.from({ length: total }, (_, i) => i + 1))
    expect(copied.map((m) => m.content)).to.deep.equal(Array.from({ length: total }, (_, i) => `message ${i + 1}`))
    expect(copied.some((m) => (m as { feedback?: unknown[] }).feedback?.length)).to.equal(false)
    expect(String(copied[1]!.citations![0]!.citationId)).to.equal(String(citationId))
    // Attachments are copied as references to the same records, not duplicated. A tool part's own
    // artifactId (a stored tool result, not a chat artifact) is left as it is.
    expect(copied[0]!.attachments).to.have.length(1)
    expect(copied[0]!.attachments![0]!.recordId).to.equal('rec-att-1')
    expect((copied[1]!.parts![0] as { artifactId?: string }).artifactId).to.equal('artifact-2')
    expect(copied[1]!.referenceData).to.have.length(1)
    expect(copied.every((m) => String(m.sessionId) === String(result.conversation._id))).to.equal(true)

    const forkRow = await ChatSession.findById(result.conversation._id).select('+nextSeq').lean()
    expect(forkRow!.nextSeq).to.equal(total)
    expect(forkRow!.isShared).to.equal(false)
    expect(forkRow!.title).to.equal('Fork of Quarterly numbers')
    expect(await ChatSessionMessage.countDocuments({ sessionId: source._id })).to.equal(total)
  })

  it('stops at the fork point when it falls inside a batch', async () => {
    const { source, messages } = await seedChat(FORK_COPY_BATCH_SIZE + 30)
    const at = FORK_COPY_BATCH_SIZE + 10
    const result = await fork(source, String(messages[at - 1]!._id))
    expect(result.messageCount).to.equal(at)
    const last = await ChatSessionMessage.findOne({ sessionId: result.conversation._id }).sort({ seq: -1 }).lean()
    expect(last!.content).to.equal(`message ${at}`)
  })

  it('refuses a message from another session and leaves no new chat behind', async () => {
    const a = await seedChat(4)
    const b = await seedChat(4)
    const before = await ChatSession.countDocuments({})
    let error: { statusCode?: number } | undefined
    try {
      await fork(a.source, String(b.messages[1]!._id))
    } catch (e) {
      error = e as { statusCode?: number }
    }
    expect(error?.statusCode).to.equal(404)
    expect(await ChatSession.countDocuments({})).to.equal(before)
  })

  it('leaves nothing behind when the copy fails part way', async () => {
    const { source, messages } = await seedChat(FORK_COPY_BATCH_SIZE * 2)
    const before = {
      sessions: await ChatSession.countDocuments({}),
      messages: await ChatSessionMessage.countDocuments({}),
    }
    const realInsert = ChatSessionMessage.insertMany.bind(ChatSessionMessage)
    let calls = 0
    sinon.stub(ChatSessionMessage, 'insertMany').callsFake(((...args: unknown[]) => {
      calls += 1
      if (calls === 2) return Promise.reject(new Error('disk full'))
      return (realInsert as (...a: unknown[]) => unknown)(...args)
    }) as never)

    let message = ''
    try {
      await fork(source, String(messages[messages.length - 1]!._id))
    } catch (e) {
      message = (e as Error).message
    }
    sinon.restore()

    expect(message).to.equal('disk full')
    expect(await ChatSession.countDocuments({})).to.equal(before.sessions)
    expect(await ChatSessionMessage.countDocuments({})).to.equal(before.messages)
  })

  it('keeps the project when the caller still has access and drops it when they do not', async () => {
    const mine = await Project.create({ orgId, userId: owner, name: 'Mine', visibility: 'private' })
    const theirs = await Project.create({ orgId, userId: stranger, name: 'Theirs', visibility: 'private' })
    const { resolveForkProjectId } = await import('../../../../src/modules/enterprise_search/utils/fork-conversation')

    const inMine = await seedChat(2, { projectId: mine._id })
    const inTheirs = await seedChat(2, { projectId: theirs._id })
    expect(String(await resolveForkProjectId(inMine.source, String(orgId), String(owner)))).to.equal(String(mine._id))
    expect(await resolveForkProjectId(inTheirs.source, String(orgId), String(owner))).to.equal(undefined)

    const kept = await fork(inMine.source, String(inMine.messages[1]!._id), {
      sessionType: 'chat',
      projectId: mine._id as mongoose.Types.ObjectId,
    })
    expect(String(kept.conversation.projectId)).to.equal(String(mine._id))
    expect(kept.conversation.projectVisibility).to.equal('private')
    const dropped = await fork(inTheirs.source, String(inTheirs.messages[1]!._id))
    expect(dropped.conversation.projectId).to.equal(undefined)
  })

  describe('through the controllers', () => {
    const run = async (
      handler: (req: never, res: never, next: never) => Promise<unknown>,
      userId: mongoose.Types.ObjectId,
      params: Record<string, string>,
    ) => {
      const out: { status?: number; body?: Record<string, unknown>; error?: { statusCode?: number } } = {}
      const res = {
        status(code: number) {
          out.status = code
          return res
        },
        json(body: Record<string, unknown>) {
          out.body = body
          return res
        },
        end: () => res,
      }
      const req = { params, body: {}, query: {}, headers: {}, user: { userId: String(userId), orgId: String(orgId) }, context: { requestId: 'r1' } }
      await handler(req as never, res as never, ((e: { statusCode?: number }) => {
        out.error = e
      }) as never)
      return out
    }

    it('forks an owned chat, answers 404 to a stranger, and the fork survives the source being deleted', async () => {
      const { source, messages, citationId } = await seedChat(6)
      const messageId = String(messages[3]!._id)

      const denied = await run(forkConversation(APP_CONFIG) as never, stranger, { conversationId: String(source._id), messageId })
      expect(denied.error?.statusCode).to.equal(404)
      expect(denied.status).to.equal(undefined)

      const ok = await run(forkConversation(APP_CONFIG) as never, owner, { conversationId: String(source._id), messageId })
      expect(ok.error).to.equal(undefined)
      expect(ok.status).to.equal(201)
      expect(ok.body!.messageCount).to.equal(4)
      const forkId = String(ok.body!.id)
      expect(forkId).to.not.equal(String(source._id))

      // What deleteConversationById writes: the session and its citations are soft-deleted.
      // The handler itself is not called: on a replica set it starts a transaction twice
      // and fails with "Transaction already in progress", independent of forking.
      await ChatSession.updateOne({ _id: source._id }, { $set: { isDeleted: true, deletedBy: owner } })
      await Citation.updateMany({ _id: citationId }, { $set: { isDeleted: true, deletedBy: owner } })

      const survivor = await ChatSession.findById(forkId).lean()
      expect(survivor!.isDeleted).to.equal(false)
      const populated = await ChatSessionMessage.find({ sessionId: forkId, seq: 2 })
        .populate({ path: 'citations.citationId', model: 'citation' })
        .lean()
      const cited = populated[0]!.citations![0]!.citationId as unknown as { _id: mongoose.Types.ObjectId; content: string } | null
      expect(cited && String(cited._id)).to.equal(String(citationId))
      expect(cited!.content).to.equal('Q3 sales were 4M.')
    })

    it('does not let a shared recipient, a project member or another org fork the chat', async () => {
      const recipient = oid()
      const projectMember = oid()
      const project = await Project.create({
        orgId,
        userId: owner,
        name: 'Shared project',
        visibility: 'private',
        members: [{ principalType: 'user', principalId: projectMember, role: 'viewer', addedBy: owner }],
      })
      const { source, messages } = await seedChat(2, {
        isShared: true,
        sharedWith: [{ userId: recipient, accessLevel: 'read' }],
        projectId: project._id,
        projectVisibility: 'project',
      })
      const params = { conversationId: String(source._id), messageId: String(messages[1]!._id) }
      const before = await ChatSession.countDocuments({})

      const asRecipient = await run(forkConversation(APP_CONFIG) as never, recipient, params)
      const asMember = await run(forkConversation(APP_CONFIG) as never, projectMember, params)
      const asOtherOrg = await (async () => {
        const out: { error?: { statusCode?: number } } = {}
        await (forkConversation(APP_CONFIG) as never as (...a: unknown[]) => Promise<unknown>)(
          { params, body: {}, query: {}, headers: {}, user: { userId: String(owner), orgId: String(oid()) }, context: { requestId: 'r' } },
          { status: () => ({ json: () => undefined }) },
          (e: { statusCode?: number }) => { out.error = e },
        )
        return out
      })()

      expect(asRecipient.error?.statusCode).to.equal(404)
      expect(asMember.error?.statusCode).to.equal(404)
      expect(asOtherOrg.error?.statusCode).to.equal(404)
      expect(await ChatSession.countDocuments({})).to.equal(before)
    })

    describe('agent conversations', () => {
      const AGENT = 'agent-1'
      const seedAgentChat = async (extra: Record<string, unknown> = {}) => {
        const seeded = await seedChat(4, {
          sessionType: 'agent',
          agentKey: AGENT,
          conversationSource: 'agent_chat',
          compactedSummary: 'older turns',
          ...extra,
        })
        await ChatSessionMessage.updateOne(
          { _id: seeded.messages[1]!._id },
          {
            $set: {
              tools: [{ toolName: 'search', toolResult: { ok: true } }],
              parts: [
                { type: 'tool_call', toolCallId: 'c-wait', status: 'awaiting_approval', approval: { approvalId: 'ap-1' } },
                { type: 'tool_call', toolCallId: 'c-done', status: 'blocked', approvalAnswer: 'declined' },
              ],
            },
          },
        )
        return seeded
      }
      const agentParams = (x: Awaited<ReturnType<typeof seedChat>>, agentKey = AGENT) => ({
        agentKey,
        conversationId: String(x.source._id),
        messageId: String(x.messages[1]!._id),
      })

      it('forks an agent chat as an agent chat of the same agent, with its tool calls', async () => {
        const x = await seedAgentChat()
        const ok = await run(forkAgentConversation(APP_CONFIG) as never, owner, agentParams(x))
        expect(ok.error).to.equal(undefined)
        expect(ok.status).to.equal(201)
        expect(ok.body!.messageCount).to.equal(2)

        const forked = await ChatSession.findById(ok.body!.id).select('+sessionType').lean()
        expect(forked!.sessionType).to.equal('agent')
        expect(forked!.agentKey).to.equal(AGENT)
        expect(forked!.conversationSource).to.equal('agent_chat')
        expect(String(forked!.userId)).to.equal(String(owner))
        expect(forked!.isShared).to.equal(false)
        expect((forked as { compactedSummary?: string }).compactedSummary).to.equal(undefined)
        const copied = await ChatSessionMessage.find({ sessionId: forked!._id }).sort({ seq: 1 }).lean()
        expect(copied[1]!.tools![0]!.toolName).to.equal('search')
        const parts = copied[1]!.parts as Array<{ toolCallId: string; status: string; approvalAnswer?: string }>
        expect(parts[0]).to.include({ toolCallId: 'c-wait', status: 'blocked', approvalAnswer: 'expired' })
        expect(parts[1]).to.include({ toolCallId: 'c-done', status: 'blocked', approvalAnswer: 'declined' })
        // The approval is still pending in the original, where it can be answered.
        const original = await ChatSessionMessage.findById(x.messages[1]!._id).lean()
        const originalParts = original!.parts as Array<{ status: string; approvalAnswer?: string }>
        expect(originalParts[0]).to.include({ status: 'awaiting_approval' })
        expect(originalParts[0]!.approvalAnswer).to.equal(undefined)
        expect(copied.map((m) => m.seq)).to.deep.equal([1, 2])
      })

      it('answers 404 for the wrong agent key, the plain route, a stranger and a project member', async () => {
        const projectMember = oid()
        const project = await Project.create({
          orgId,
          userId: owner,
          name: 'Agents project',
          visibility: 'private',
          members: [{ principalType: 'user', principalId: projectMember, role: 'viewer', addedBy: owner }],
        })
        const x = await seedAgentChat({ projectId: project._id, projectVisibility: 'project' })
        const before = await ChatSession.countDocuments({})

        const wrongAgent = await run(forkAgentConversation(APP_CONFIG) as never, owner, agentParams(x, 'agent-OTHER'))
        const plainRoute = await run(forkConversation(APP_CONFIG) as never, owner, {
          conversationId: String(x.source._id),
          messageId: String(x.messages[1]!._id),
        })
        const asStranger = await run(forkAgentConversation(APP_CONFIG) as never, stranger, agentParams(x))
        const asMember = await run(forkAgentConversation(APP_CONFIG) as never, projectMember, agentParams(x))

        for (const out of [wrongAgent, plainRoute, asStranger, asMember]) {
          expect(out.error?.statusCode).to.equal(404)
        }
        expect(await ChatSession.countDocuments({})).to.equal(before)
      })

      it('does not fork an assistant chat through the agent route', async () => {
        const x = await seedChat(2)
        const out = await run(forkAgentConversation(APP_CONFIG) as never, owner, agentParams(x))
        expect(out.error?.statusCode).to.equal(404)
      })
    })

    ;(useTransactions ? it.skip : it)('deleting the fork leaves the original chat and its citations alone', async () => {
      const { source, messages, citationId } = await seedChat(4)
      const forked = await run(forkConversation(APP_CONFIG) as never, owner, {
        conversationId: String(source._id),
        messageId: String(messages[3]!._id),
      })
      const forkId = String(forked.body!.id)
      // Deleting a chat first asks the connectors service to delete its artifacts.
      sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()

      const deleted = await run(deleteConversationById(APP_CONFIG) as never, owner, { conversationId: forkId })
      expect(deleted.error).to.equal(undefined)
      expect(deleted.status).to.equal(200)

      expect((await ChatSession.findById(forkId).lean())!.isDeleted).to.equal(true)
      expect((await ChatSession.findById(source._id).lean())!.isDeleted).to.equal(false)
      expect(await ChatSessionMessage.countDocuments({ sessionId: source._id })).to.equal(4)
      expect((await Citation.findById(citationId).lean())).to.not.equal(null)
    })
  })
})
