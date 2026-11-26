/**
 * FORKFIX on a MongoDB replica set: both fork features against the real
 * conversation store, the conversation reads, the run gate every run-scoped
 * route takes (`SessionRunService.streamAccess`), the workspace version list
 * and conversation erasure. Reproduces a reported bug: a forked
 * agent chat's answers named the source's runs, so the web app asked
 * `…/runs/{run}/timeline`, `…/followups` and `…/workspace/versions` under the
 * fork and got 404 three times.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import mongoose, { ClientSession, Types } from 'mongoose'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command'
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema'
import {
  forkAgentConversation,
  forkConversation,
  getAgentConversationById,
  getConversationById,
} from '../../../src/modules/enterprise_search/controller/es_controller'
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo'
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo'
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo'
import { HarnessSessionForkStore } from '../../../src/libs/repositories/agent-harness/session-forks/mongo'
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema'
import { AgentSessionForkModel } from '../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema'
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service'
import { SessionWorkspaceService } from '../../../src/modules/agent-harness/application/session-workspace-service'
import type { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service'
import type { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service'
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors'
import { FOREIGN_TURN_STUB } from '../../../src/modules/agent-harness/domain/session-audience'
import { planSessionImport } from '../../../src/modules/agent-harness/domain/session-import'
import { forkTranscript } from '../../../src/modules/agent-harness/domain/session-forks'
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution'
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels } from '../agent-harness/support/harness-mongo'

registerHarnessModels([Citation, AgentSessionForkModel] as unknown as Array<mongoose.Model<never>>)

const URI = process.env.MONGO_IT_URI
const describeIfMongo = URI ? describe : describe.skip
// The conversation read asks the query service (aiBackend) whether its reader can still read a cited record.
const APP_CONFIG = { connectorBackend: 'http://connectors.test', aiBackend: 'http://query.test', jwtSecret: 'forkfix-jwt', scopedJwtSecret: 'forkfix-scoped' } as never
const AGENT = 'b2b523bb-9a53-407e-a3f6-011ba237feb3'
const oid = () => new Types.ObjectId()

/** Every run id a value still names, on a message or in any part. */
const runIdsIn = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.flatMap(runIdsIn)
  if (value === null || typeof value !== 'object' || value instanceof Types.ObjectId) return []
  return Object.entries(value).flatMap(([key, item]) =>
    (key === 'runId' || key === 'parentRunId') && typeof item === 'string' ? [item] : runIdsIn(item),
  )
}

const notFound = async (work: Promise<unknown>): Promise<void> => {
  try {
    await work
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessNotFoundError)
    return
  }
  expect.fail('expected 404 (HarnessNotFoundError)')
}

describeIfMongo('FORKFIX: forked conversations never ask for another conversation\'s runs (MongoDB)', function () {
  this.timeout(120_000)
  const orgId = oid()
  const owner = oid()
  const stranger = oid()
  const viewer = oid()
  const sessions = new HarnessChatSessionRepository()
  const runGate = new SessionRunService(
    {
      get: async (org: string, runId: string) =>
        (await AgentRunModel.findOne({ orgId: org, runId }).lean()) as unknown as HarnessRun | null,
    } as unknown as ExecutionService,
    sessions,
  )
  const versions = new SessionWorkspaceService(sessions, {
    versions: async (scope: { sessionId: string }) => ({ workspaceId: `ws:${scope.sessionId}`, head: 0, versions: [] }),
  } as unknown as WorkspaceService)

  // A conversation read hides an answer whose cited record its reader cannot read now (#5698): the query service's
  // access check answers here that the seeded record is readable, and records what it was asked.
  const accessAsked: string[][] = []
  let access: sinon.SinonStub | undefined
  before(connectHarnessMongo)
  before(() => {
    access = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: { uri: string; body?: string }) {
      if (!this.uri.endsWith('/chat/records/access')) return Promise.resolve({ statusCode: 200, data: {}, msg: '' } as never)
      accessAsked.push((JSON.parse(this.body ?? '{}') as { recordIds?: string[] }).recordIds ?? [])
      return Promise.resolve({ statusCode: 200, data: { accessible: ['rec-1'], accessibleUrls: [] }, msg: '' } as never)
    })
  })
  after(() => access?.restore())
  after(disconnectHarnessMongo)

  const seedRun = (runId: string, sessionId: Types.ObjectId, status = 'completed') =>
    AgentRunModel.collection.insertOne({
      orgId: String(orgId),
      runId,
      sessionId: String(sessionId),
      actorId: String(owner),
      status,
      harnessMajor: 2,
      revision: 1,
    })

  /** A durable conversation as the harness writes one: two answered turns, a coding child under the first. */
  const seedDurable = async (opts: { agent: boolean; shared?: Array<{ userId: Types.ObjectId; accessLevel: string }>; collaborator?: Types.ObjectId }) => {
    const sessionId = oid()
    const runs = [`run-${String(sessionId)}-1`, `run-${String(sessionId)}-2`]
    const child = `child-${String(sessionId)}`
    await ChatSession.create({
      _id: sessionId,
      orgId,
      userId: owner,
      initiator: owner,
      title: 'Primes',
      harnessVersion: 'durable-v1',
      lastActivityAt: 1,
      ...(opts.shared ? { isShared: true, sharedWith: opts.shared } : {}),
      ...(opts.agent ? { sessionType: 'agent', agentKey: AGENT, conversationSource: 'agent_chat' } : { sessionType: 'chat' }),
    })
    const citation = await Citation.create({
      content: 'Primes are integers > 1.',
      chunkIndex: 0,
      citationType: 'vectordb|document',
      metadata: { orgId: String(orgId), recordId: 'rec-1', recordName: 'primes.md', origin: 'UPLOAD', mimeType: 'text/markdown' },
    })
    const rows = [
      { messageType: 'user_query', content: 'Print the first ten primes' },
      {
        messageType: 'bot_response',
        content: '2 3 5 7 11 13 17 19 23 29',
        runId: runs[0],
        citations: [{ citationId: citation._id, relevanceScore: 0.8 }],
        parts: [
          { type: 'text', runId: runs[0], text: 'Planning' },
          { type: 'sub_agent', runId: child, parentRunId: runs[0], status: 'completed', codeTaskGoal: 'run it' },
          { type: 'engine_task', runId: child, taskId: child, frames: [] },
          { type: 'text', runId: runs[0], text: 'Done', isFinal: true },
        ],
      },
      { messageType: 'user_query', content: 'And the next ten?', ...(opts.collaborator ? { senderId: opts.collaborator } : {}) },
      {
        messageType: 'bot_response',
        content: opts.collaborator ? 'SECRET collaborator answer' : '31 37 41 43 47 53 59 61 67 71',
        runId: runs[1],
        parts: [{ type: 'text', runId: runs[1], text: 'More' }],
        ...(opts.collaborator ? { senderId: opts.collaborator } : {}),
      },
    ]
    const messages = await ChatSessionMessage.insertMany(
      rows.map((row, i) => ({ ...row, sessionId, orgId, seq: i + 1 })),
      { ordered: true },
    )
    await ChatSession.updateOne({ _id: sessionId }, { $set: { nextSeq: rows.length } })
    for (const runId of [...runs, child]) await seedRun(runId, sessionId)
    return { sessionId, runs, child, messages, citationId: citation._id }
  }

  const call = async (
    handler: (req: never, res: never, next: never) => Promise<unknown>,
    userId: Types.ObjectId,
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

  const forkAt = async (agent: boolean, sessionId: Types.ObjectId, messageId: unknown, as = owner) =>
    agent
      ? call(forkAgentConversation(APP_CONFIG) as never, as, { agentKey: AGENT, conversationId: String(sessionId), messageId: String(messageId) })
      : call(forkConversation(APP_CONFIG) as never, as, { conversationId: String(sessionId), messageId: String(messageId) })

  /** What the web app reads on open: the conversation GET. */
  const open = async (agent: boolean, conversationId: string, as = owner) => {
    const got = agent
      ? await call(getAgentConversationById(APP_CONFIG, {}) as never, as, { agentKey: AGENT, conversationId })
      : await call(getConversationById(APP_CONFIG, {}) as never, as, { conversationId })
    return got
  }

  const scope = (agent: boolean, sessionId: string, actor = owner) => ({
    orgId: String(orgId),
    sessionId,
    actorId: String(actor),
    ...(agent ? { agentKey: AGENT } : {}),
  })

  for (const agent of [true, false]) {
    const kind = agent ? 'agent chat' : 'assistant chat'

    describe(`main's fork from a message: ${kind}`, () => {
      it('regression (three 404s): the fork names no source run, so timeline, followups and workspace versions are never asked for one; versions answer for the fork itself', async () => {
        const source = await seedDurable({ agent })
        const forked = await forkAt(agent, source.sessionId, source.messages[3]!._id)
        expect(forked.error).to.equal(undefined)
        expect(forked.status).to.equal(201)
        const forkId = String(forked.body!['id'])

        const stored = await ChatSessionMessage.find({ sessionId: forkId }).sort({ seq: 1 }).lean()
        expect(stored).to.have.length(4)
        expect(runIdsIn(stored)).to.deep.equal([])
        expect(stored.every((message) => (message as { copied?: boolean }).copied === true)).to.equal(true)
        expect(stored[1]!.parts!.map((part) => part.type)).to.deep.equal(['text', 'sub_agent', 'engine_task', 'text'])

        const opened = await open(agent, forkId)
        expect(opened.error).to.equal(undefined)
        const conversation = opened.body!['conversation'] as { durable: boolean; messages: Array<Record<string, unknown>> }
        expect(runIdsIn(conversation.messages)).to.deep.equal([])
        expect(conversation.messages.filter((m) => m['messageType'] === 'bot_response').every((m) => m['copied'] === true)).to.equal(true)
        // Keeps the source's executor: the next turn runs durably and the workspace routes know the fork.
        expect(conversation.durable).to.equal(true)
        const row = await ChatSession.findById(forkId).select('+harnessVersion +harnessLedger').lean()
        expect(row!.harnessVersion).to.equal('durable-v1')
        expect(row!.harnessLedger!.seq).to.equal(0)
        expect(await versions.versions(scope(agent, forkId), { limit: 50 })).to.deep.include({ head: 0, versions: [] })

        // The run gate stays per conversation: the source's run is not the fork's, for its owner either.
        for (const runId of [...source.runs, source.child]) {
          await notFound(runGate.streamAccess(scope(agent, forkId), runId))
          await runGate.streamAccess(scope(agent, String(source.sessionId)), runId)
        }
      })

      it('copies citations into documents of its own: erasing the source leaves the fork whole', async () => {
        const source = await seedDurable({ agent })
        const forked = await forkAt(agent, source.sessionId, source.messages[3]!._id)
        const forkId = String(forked.body!['id'])
        const copiedLink = (await ChatSessionMessage.findOne({ sessionId: forkId, seq: 2 }).lean())!.citations![0]!
        expect(String(copiedLink.citationId)).to.not.equal(String(source.citationId))

        await new HarnessConversationEraser({
          eraseAttachments: async () => ({ attachments: 0, blobs: 0, refused: 0, unrecorded: 0, removed: [] }),
        }).erase(String(orgId), String(source.sessionId))
        expect(await Citation.findById(source.citationId).lean()).to.equal(null)

        accessAsked.length = 0
        const opened = await open(agent, forkId)
        expect(accessAsked.flat()).to.include('rec-1')
        expect(opened.error).to.equal(undefined)
        const messages = (opened.body!['conversation'] as { messages: Array<{ content: string; citations: Array<{ citationData: { content: string } | null }> }> }).messages
        const answer = messages.find((m) => m.content.startsWith('2 3 5'))!
        expect(answer.citations[0]!.citationData!.content).to.equal('Primes are integers > 1.')
        await notFound(runGate.streamAccess(scope(agent, forkId), source.runs[0]!))
      })

      it('from an earlier message: only the history up to it, detached; the source keeps its runs', async () => {
        const source = await seedDurable({ agent })
        const forked = await forkAt(agent, source.sessionId, source.messages[1]!._id)
        const forkId = String(forked.body!['id'])
        const stored = await ChatSessionMessage.find({ sessionId: forkId }).sort({ seq: 1 }).lean()
        expect(stored.map((m) => m.content)).to.deep.equal(['Print the first ten primes', '2 3 5 7 11 13 17 19 23 29'])
        expect(runIdsIn(stored)).to.deep.equal([])
        const original = await ChatSessionMessage.find({ sessionId: source.sessionId }).sort({ seq: 1 }).lean()
        expect(original.map((m) => m.runId ?? null)).to.deep.equal([null, source.runs[0], null, source.runs[1]])
      })

      it('a new turn in the fork: it binds durably, imports every copied message, and its own run is read under the fork', async () => {
        const source = await seedDurable({ agent })
        const forkId = String((await forkAt(agent, source.sessionId, source.messages[3]!._id)).body!['id'])
        const tx: ClientSession = await mongoose.startSession()
        try {
          await tx.withTransaction(async () => {
            expect(await sessions.bindDurableSession(scope(agent, forkId), Date.now(), tx)).to.not.equal(null)
            const history = await new HarnessHistorySource().read({ orgId: String(orgId), sessionId: forkId }, tx)
            const { plan } = planSessionImport(history, false)
            expect(JSON.stringify(plan!.turns)).to.include('Print the first ten primes').and.include('31 37 41')
          })
        } finally {
          await tx.endSession()
        }
        const own = `own-${forkId}`
        await seedRun(own, new Types.ObjectId(forkId))
        await ChatSessionMessage.create({ sessionId: forkId, orgId, seq: 5, messageType: 'bot_response', content: 'new', runId: own })
        const opened = await open(agent, forkId)
        const messages = (opened.body!['conversation'] as { messages: Array<Record<string, unknown>> }).messages
        expect(runIdsIn(messages)).to.deep.equal([own])
        await runGate.streamAccess(scope(agent, forkId), own)
      })

      it('a fork of a fork: copied messages stay copied, the first fork\'s own run is detached in the second', async () => {
        const source = await seedDurable({ agent })
        const first = String((await forkAt(agent, source.sessionId, source.messages[3]!._id)).body!['id'])
        const own = `own-${first}`
        await seedRun(own, new Types.ObjectId(first))
        const ownAnswer = await ChatSessionMessage.create({
          sessionId: first, orgId, seq: 5, messageType: 'bot_response', content: 'fork answer', runId: own, parts: [{ type: 'text', runId: own }],
        })
        await ChatSession.updateOne({ _id: first }, { $set: { nextSeq: 5 } })
        const second = await forkAt(agent, new Types.ObjectId(first), ownAnswer._id)
        expect(second.status).to.equal(201)
        const secondId = String(second.body!['id'])
        const stored = await ChatSessionMessage.find({ sessionId: secondId }).sort({ seq: 1 }).lean()
        expect(stored).to.have.length(5)
        expect(runIdsIn(stored)).to.deep.equal([])
        expect(stored.every((m) => (m as { copied?: boolean }).copied === true)).to.equal(true)
        await notFound(runGate.streamAccess(scope(agent, secondId), own))
      })

      it("another user or a shared viewer cannot fork it (owner only), nor reach the source's runs through the owner's fork", async () => {
        const source = await seedDurable({ agent, shared: [{ userId: viewer, accessLevel: 'read' }] })
        for (const who of [stranger, viewer]) {
          const refused = await forkAt(agent, source.sessionId, source.messages[3]!._id, who)
          expect(refused.error?.statusCode).to.equal(404)
        }
        const forkId = String((await forkAt(agent, source.sessionId, source.messages[3]!._id)).body!['id'])
        for (const who of [stranger, viewer]) {
          expect((await open(agent, forkId, who)).error?.statusCode).to.equal(404)
          await notFound(runGate.streamAccess(scope(agent, forkId, who), source.runs[1]!))
          await notFound(versions.versions(scope(agent, forkId, who), {}))
        }
      })

      it("a collaborator's turn (SS2) becomes a stub: neither the fork nor its next run's history holds their answer", async () => {
        const collaborator = oid()
        const source = await seedDurable({ agent, shared: [{ userId: collaborator, accessLevel: 'write' }], collaborator })
        const forkId = String((await forkAt(agent, source.sessionId, source.messages[3]!._id)).body!['id'])
        const stored = await ChatSessionMessage.find({ sessionId: forkId }).sort({ seq: 1 }).lean()
        expect(stored.map((m) => (m as { foreignTurn?: boolean }).foreignTurn === true)).to.deep.equal([false, false, true, true])
        expect(stored[3]!.content).to.equal(FOREIGN_TURN_STUB)
        expect(JSON.stringify(stored)).to.not.include('SECRET')
        const tx = await mongoose.startSession()
        try {
          const history = await tx.withTransaction(() => new HarnessHistorySource().read({ orgId: String(orgId), sessionId: forkId }, tx))
          expect(JSON.stringify(history)).to.not.include('SECRET')
        } finally {
          await tx.endSession()
        }
      })

      it('while a run still runs in the source: only finished turns are copied, and the running run stays the source\'s', async () => {
        const source = await seedDurable({ agent })
        const running = `running-${String(source.sessionId)}`
        await seedRun(running, source.sessionId, 'running')
        const forkId = String((await forkAt(agent, source.sessionId, source.messages[3]!._id)).body!['id'])
        expect(await ChatSessionMessage.countDocuments({ sessionId: forkId })).to.equal(4)
        await notFound(runGate.streamAccess(scope(agent, forkId), running))
        expect((await AgentRunModel.findOne({ runId: running }).lean())!.sessionId).to.equal(String(source.sessionId))
      })
    })

    describe(`a fork written before FORKFIX (968ca2090): ${kind}`, () => {
      it('the conversation read detaches the copied run ids, and keeps the fork\'s own', async () => {
        const source = await seedDurable({ agent })
        const forkId = oid()
        await ChatSession.create({
          _id: forkId,
          orgId,
          userId: owner,
          initiator: owner,
          title: 'Fork of Primes',
          lastActivityAt: 1,
          metadata: new Map([['forkedFromConversationId', String(source.sessionId)], ['forkedFromMessageId', String(source.messages[3]!._id)]]),
          ...(agent ? { sessionType: 'agent', agentKey: AGENT, conversationSource: 'agent_chat' } : { sessionType: 'chat' }),
        })
        const copied = await ChatSessionMessage.find({ sessionId: source.sessionId }).sort({ seq: 1 }).lean()
        await ChatSessionMessage.insertMany(
          copied.map((message) => ({ ...Object.fromEntries(Object.entries(message).filter(([key]) => key !== '_id')), sessionId: forkId })),
        )
        const own = `own-${String(forkId)}`
        await seedRun(own, forkId)
        await ChatSessionMessage.create({ sessionId: forkId, orgId, seq: 5, messageType: 'bot_response', content: 'new', runId: own })

        const opened = await open(agent, String(forkId))
        expect(opened.error).to.equal(undefined)
        const messages = (opened.body!['conversation'] as { messages: Array<Record<string, unknown>> }).messages
        expect(runIdsIn(messages)).to.deep.equal([own])
        expect(messages.filter((m) => m['copied'] === true)).to.have.length(2)
      })
    })

    describe(`continue privately: ${kind}`, () => {
      it("copies turns marked copied with no run id; the source's runs stay unreachable through the copy", async () => {
        const source = await seedDurable({ agent })
        const store = new HarnessSessionForkStore()
        const read = (await store.source(scope(agent, String(source.sessionId))))!
        const forkSessionId = String(oid())
        const key = { orgId: String(orgId), sourceSessionId: String(source.sessionId), actorId: String(owner), requestKey: `k-${forkSessionId}` }
        await store.begin(key, forkSessionId, 1)
        const tx = await mongoose.startSession()
        try {
          await tx.withTransaction(() =>
            store.commit(
              {
                key,
                forkSessionId,
                source: { sessionType: read.sessionType, agentKey: read.agentKey, title: read.title, modelInfo: read.modelInfo, durable: read.durable },
                messages: forkTranscript(read.messages, String(owner), read.participants.ownerId),
                result: { sessionId: forkSessionId, fromSessionId: String(source.sessionId), atSeq: 0, role: 'owner', counts: { messages: 4, stubs: 0, citations: 1, citationsHidden: 0, artifacts: 0, artifactsHidden: 0, files: 0 } },
                now: 2,
              },
              tx,
            ),
          )
        } finally {
          await tx.endSession()
        }
        const opened = await open(agent, forkSessionId)
        expect(opened.error).to.equal(undefined)
        const conversation = opened.body!['conversation'] as { durable: boolean; messages: Array<Record<string, unknown>> }
        expect(conversation.durable).to.equal(true)
        expect(runIdsIn(conversation.messages)).to.deep.equal([])
        expect(conversation.messages.every((m) => m['copied'] === true)).to.equal(true)
        expect(await versions.versions(scope(agent, forkSessionId), {})).to.deep.include({ head: 0 })
        for (const runId of source.runs) await notFound(runGate.streamAccess(scope(agent, forkSessionId), runId))
      })
    })
  }
})
