import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { ForbiddenError, NotFoundError, InternalServerError } from '../../../../src/libs/errors/http.errors'
import {
  runsByAuthor,
  buildForkTitle,
  FORK_TITLE_MAX_LENGTH,
  FORK_TITLE_PREFIX,
  stripMessageForCopy,
  copyArtifactsForFork,
  copySessionUpToMessage,
  resolveForkProjectId,
  expirePendingApprovals,
  FORK_COPY_BATCH_SIZE,
} from '../../../../src/modules/enterprise_search/utils/fork-conversation'
import { InMemoryChatStore, oid } from '../controller/chat-test-harness'

describe('fork-conversation helpers', () => {
  it('buildForkTitle prefixes the original title', () => {
    expect(buildForkTitle('Q3 review')).to.equal(`${FORK_TITLE_PREFIX}Q3 review`)
  })

  it('buildForkTitle falls back when the title is blank', () => {
    expect(buildForkTitle('   ')).to.equal(`${FORK_TITLE_PREFIX}Untitled`)
    expect(buildForkTitle(undefined)).to.equal(`${FORK_TITLE_PREFIX}Untitled`)
  })

  it('buildForkTitle stays within the title length limit', () => {
    const titled = buildForkTitle('x'.repeat(300))
    expect(titled.length).to.equal(FORK_TITLE_MAX_LENGTH)
    expect(titled.startsWith(FORK_TITLE_PREFIX)).to.equal(true)
  })

  it('stripMessageForCopy drops identity, addressing, and feedback', () => {
    const copied = stripMessageForCopy({
      _id: new Types.ObjectId(),
      sessionId: new Types.ObjectId(),
      orgId: new Types.ObjectId(),
      seq: 4,
      createdAt: new Date(),
      updatedAt: new Date(),
      feedback: [{ isHelpful: true }],
      messageType: 'bot_response',
      content: 'Q3 sales were 4M.',
      citations: [{ citationId: new Types.ObjectId(), relevanceScore: 0.9 }],
    })
    expect(copied).to.deep.include({
      messageType: 'bot_response',
      content: 'Q3 sales were 4M.',
    })
    expect(copied).to.not.have.property('_id')
    expect(copied).to.not.have.property('sessionId')
    expect(copied).to.not.have.property('seq')
    expect(copied).to.not.have.property('feedback')
    expect(copied.citations).to.have.length(1)
  })
})

describe('copySessionUpToMessage', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('copies the session and messages up to the fork point, leaving the source unchanged', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const orgId = oid()
    const owner = oid()
    const source = store.addSession({
      orgId,
      userId: owner,
      initiator: owner,
      title: 'Quarterly numbers',
      sessionType: 'chat',
    })
    store.addMessage(source, { messageType: 'user_query', content: 'What were Q3 sales?' })
    const mid = store.addMessage(source, { messageType: 'bot_response', content: 'Q3 sales were 4M.' })
    store.addMessage(source, { messageType: 'user_query', content: 'And Q4?' })
    store.addMessage(source, { messageType: 'bot_response', content: 'Q4 is not in yet.' })
    const sourceMessageCount = store.messagesOf(source._id).length

    const result = await copySessionUpToMessage(
      source,
      String(mid._id),
      String(owner),
      String(orgId),
      { sessionType: 'chat' },
    )

    expect(result.messageCount).to.equal(2)
    expect(result.conversation.title).to.equal('Fork of Quarterly numbers')
    expect(String(result.conversation.userId)).to.equal(String(owner))
    expect(result.conversation.isShared).to.equal(false)
    expect(store.messagesOf(source._id)).to.have.length(sourceMessageCount)
    expect(store.session(source._id)?.title).to.equal('Quarterly numbers')

    const forked = store.messagesOf(result.conversation._id)
    expect(forked.map((m) => m.content)).to.deep.equal([
      'What were Q3 sales?',
      'Q3 sales were 4M.',
    ])
    expect(forked.every((m) => String(m.sessionId) === String(result.conversation._id))).to.equal(true)
  })

  it('rejects a message that does not belong to the source session', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const orgId = oid()
    const owner = oid()
    const source = store.addSession({
      orgId,
      userId: owner,
      initiator: owner,
      title: 'A',
      sessionType: 'chat',
    })
    store.addMessage(source, { messageType: 'user_query', content: 'Hello' })

    try {
      await copySessionUpToMessage(
        source,
        String(oid()),
        String(owner),
        String(orgId),
        { sessionType: 'chat' },
      )
      expect.fail('expected NotFoundError')
    } catch (error) {
      expect((error as { statusCode?: number }).statusCode).to.equal(404)
    }
    expect(store.sessions).to.have.length(1)
  })

  const seed = (store: InMemoryChatStore, count: number, extra: Record<string, unknown> = {}) => {
    const orgId = oid()
    const owner = oid()
    const source = store.addSession({
      orgId,
      userId: owner,
      initiator: owner,
      title: 'Long thread',
      sessionType: 'chat',
      ...extra,
    })
    const rows = Array.from({ length: count }, (_, i) =>
      store.addMessage(source, { messageType: i % 2 ? 'bot_response' : 'user_query', content: `m${i + 1}` }),
    )
    return { source, rows, owner, orgId }
  }

  it('copies a thread longer than one batch in order and stops at the fork point', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const total = FORK_COPY_BATCH_SIZE * 2 + 30
    const { source, rows, owner, orgId } = seed(store, total + 10)
    const at = rows[total - 1]!

    const result = await copySessionUpToMessage(source, String(at._id), String(owner), String(orgId), {
      sessionType: 'chat',
    })

    expect(result.messageCount).to.equal(total)
    const copied = store.messagesOf(result.conversation._id)
    expect(copied.map((m) => m.content)).to.deep.equal(Array.from({ length: total }, (_, i) => `m${i + 1}`))
    expect(copied.map((m) => m.seq)).to.deep.equal(Array.from({ length: total }, (_, i) => i + 1))
  })

  it('removes the new session and what it already copied when the copy fails without a transaction', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const { source, rows, owner, orgId } = seed(store, FORK_COPY_BATCH_SIZE + 5)
    ;(ChatSessionMessage.insertMany as unknown as sinon.SinonStub).onSecondCall().rejects(new InternalServerError('disk full'))
    const deleteMessages = sinon.stub(ChatSessionMessage, 'deleteMany').resolves({} as never)
    const deleteSession = sinon.stub(ChatSession, 'deleteOne').resolves({} as never)

    try {
      await copySessionUpToMessage(source, String(rows[rows.length - 1]!._id), String(owner), String(orgId), {
        sessionType: 'chat',
      })
      expect.fail('expected the copy to fail')
    } catch (error) {
      expect((error as Error).message).to.equal('disk full')
    }

    expect(deleteMessages.calledOnce).to.equal(true)
    expect(deleteSession.calledOnce).to.equal(true)
    const forkId = (deleteSession.firstCall.args[0] as { _id: Types.ObjectId })._id
    expect(String((deleteMessages.firstCall.args[0] as { sessionId: Types.ObjectId }).sessionId)).to.equal(String(forkId))
  })

  it('leaves the rollback to the transaction when one is passed', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const { source, rows, owner, orgId } = seed(store, 3)
    ;(ChatSessionMessage.insertMany as unknown as sinon.SinonStub).onFirstCall().rejects(new Error('boom'))
    const deleteMessages = sinon.stub(ChatSessionMessage, 'deleteMany').resolves({} as never)
    const deleteSession = sinon.stub(ChatSession, 'deleteOne').resolves({} as never)

    try {
      await copySessionUpToMessage(
        source,
        String(rows[2]!._id),
        String(owner),
        String(orgId),
        { sessionType: 'chat' },
        {} as never,
      )
      expect.fail('expected the copy to fail')
    } catch (error) {
      expect((error as Error).message).to.equal('boom')
    }
    expect(deleteMessages.called).to.equal(false)
    expect(deleteSession.called).to.equal(false)
  })

  it('links the fork to a project only when the caller passes one', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const projectId = oid()
    const { source, rows, owner, orgId } = seed(store, 2, { projectId })
    const target = String(rows[1]!._id)

    const kept = await copySessionUpToMessage(source, target, String(owner), String(orgId), { sessionType: 'chat', projectId })
    expect(String(kept.conversation.projectId)).to.equal(String(projectId))
    expect(kept.conversation.projectVisibility).to.equal('private')

    const dropped = await copySessionUpToMessage(source, target, String(owner), String(orgId), { sessionType: 'chat' })
    expect(dropped.conversation.projectId).to.equal(undefined)
  })

  describe('resolveForkProjectId', () => {
    const chatIn = (projectId?: Types.ObjectId) => ({ projectId }) as never

    it('is undefined for a chat outside any project, without asking the project service', async () => {
      const assert = sinon.stub(ProjectService, 'assertAccess')
      expect(await resolveForkProjectId(chatIn(), 'org', 'user')).to.equal(undefined)
      expect(assert.called).to.equal(false)
    })

    it('returns the project while the caller still has viewer access', async () => {
      const projectId = oid()
      const assert = sinon.stub(ProjectService, 'assertAccess').resolves({} as never)
      expect(String(await resolveForkProjectId(chatIn(projectId), 'org', 'user'))).to.equal(String(projectId))
      expect(assert.firstCall.args).to.deep.equal(['org', 'user', String(projectId), 'viewer'])
    })

    it('drops the project once the caller cannot see it', async () => {
      const stub = sinon.stub(ProjectService, 'assertAccess')
      stub.onFirstCall().rejects(new NotFoundError('Project not found'))
      stub.onSecondCall().rejects(new ForbiddenError('no'))
      expect(await resolveForkProjectId(chatIn(oid()), 'org', 'user')).to.equal(undefined)
      expect(await resolveForkProjectId(chatIn(oid()), 'org', 'user')).to.equal(undefined)
    })

    it('does not hide an unexpected failure as a lost project', async () => {
      sinon.stub(ProjectService, 'assertAccess').rejects(new Error('db down'))
      try {
        await resolveForkProjectId(chatIn(oid()), 'org', 'user')
        expect.fail('expected the error to surface')
      } catch (error) {
        expect((error as Error).message).to.equal('db down')
      }
    })
  })

  describe('pending tool approvals in a fork', () => {
    const waiting = { type: 'tool_call', toolCallId: 'c1', status: 'awaiting_approval', approval: { approvalId: 'ap-1' } } as never

    it('settles a waiting call as expired, the way the original does when the window runs out', () => {
      const [part] = expirePendingApprovals([waiting])!
      expect(part).to.deep.include({ status: 'blocked', approvalAnswer: 'expired', resultPreview: 'The approval request expired.' })
      expect((part as { approval: unknown }).approval).to.deep.equal({ approvalId: 'ap-1' })
    })

    it('reaches a call a sub-agent asked for, at any depth', () => {
      const parts = [{ type: 'sub_agent', runId: 'r', parts: [{ type: 'sub_agent', runId: 'r2', parts: [waiting] }] }] as never
      const inner = (expirePendingApprovals(parts)![0] as any).parts[0].parts[0]
      expect(inner.approvalAnswer).to.equal('expired')
    })

    it('leaves answered calls, other calls and other parts alone, and returns the same objects', () => {
      const answered = { type: 'tool_call', status: 'blocked', approvalAnswer: 'declined' } as never
      const running = { type: 'tool_call', status: 'running' } as never
      const text = { type: 'text', content: 'hi' } as never
      const nested = { type: 'sub_agent', parts: [answered, text] } as never
      const out = expirePendingApprovals([answered, running, text, nested])!
      expect(out[0]).to.equal(answered)
      expect(out[1]).to.equal(running)
      expect(out[2]).to.equal(text)
      expect(out[3]).to.equal(nested)
      expect(expirePendingApprovals(undefined)).to.equal(undefined)
    })

    it('does not touch the part it was given', () => {
      const original = { type: 'tool_call', status: 'awaiting_approval' } as never
      expirePendingApprovals([original])
      expect((original as { status: string }).status).to.equal('awaiting_approval')
      expect((original as { approvalAnswer?: string }).approvalAnswer).to.equal(undefined)
    })

    it('expires the copy and leaves the original message waiting', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const { source, rows, owner, orgId } = seed(store, 2)
      ;(rows[1] as any).set('parts', [waiting])

      const result = await copySessionUpToMessage(source, String(rows[1]!._id), String(owner), String(orgId), { sessionType: 'chat' })

      const copied = store.messagesOf(result.conversation._id)[1] as any
      expect(copied.parts[0].approvalAnswer).to.equal('expired')
      const original = store.messagesOf(source._id)[1] as any
      expect(original.parts[0].status).to.equal('awaiting_approval')
      expect(original.parts[0].approvalAnswer).to.equal(undefined)
    })
  })
})

describe('copySessionUpToMessage with artifacts', () => {
  afterEach(() => {
    sinon.restore()
  })

  const card = (version: number) =>
    `::artifact[report.md](record:old){text/markdown|doc-old|old|DOCUMENT|${version}}`
  const copied = {
    sourceArtifactId: 'old',
    sourceDocumentId: 'doc-old',
    artifactId: 'new',
    documentId: 'doc-new',
    versions: [
      { from: 1, to: 1 },
      { from: 2, to: 2 },
    ],
  }

  const seedWithArtifacts = (store: InMemoryChatStore) => {
    const orgId = oid()
    const owner = oid()
    const source = store.addSession({ orgId, userId: owner, initiator: owner, title: 'Report', sessionType: 'chat' })
    store.addMessage(source, { messageType: 'user_query', content: 'write a report' })
    store.addMessage(source, {
      messageType: 'bot_response',
      content: `Done\n\n${card(1)}`,
      parts: [{ type: 'tool_call', toolCallId: 'c1', args: '{"artifact_id":"old"}', resultPreview: 'saved old' }],
    })
    store.addMessage(source, { messageType: 'user_query', content: 'edit it' })
    const forkPoint = store.addMessage(source, { messageType: 'bot_response', content: `Edited\n\n${card(2)}` })
    store.addMessage(source, { messageType: 'user_query', content: 'edit again' })
    store.addMessage(source, { messageType: 'bot_response', content: `Edited again\n\n${card(3)}` })
    return { source, forkPoint, owner, orgId }
  }

  // The two steps runFork takes: copy first, outside any transaction, then write the chat.
  const fork = async (
    { source, forkPoint, owner, orgId }: ReturnType<typeof seedWithArtifacts>,
    copyArtifacts: sinon.SinonStub,
  ) => {
    const artifacts = await copyArtifactsForFork(
      source,
      String(forkPoint._id),
      new Types.ObjectId(),
      copyArtifacts as never,
    )
    return copySessionUpToMessage(
      source,
      String(forkPoint._id),
      String(owner),
      String(orgId),
      { sessionType: 'chat' },
      null,
      artifacts,
    )
  }

  it('copies each artifact up to the fork point and points the copied messages at the copies', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const copyArtifacts = sinon.stub().resolves({ copies: [copied], failed: [] })

    const result = await fork(seeded, copyArtifacts)

    expect(copyArtifacts.calledOnce).to.equal(true)
    expect(copyArtifacts.firstCall.args[0]).to.equal(String(result.conversation._id))
    expect(copyArtifacts.firstCall.args[1]).to.deep.equal([{ artifactId: 'old', upToVersion: 2 }])

    const rows = store.messagesOf(result.conversation._id) as any[]
    expect(rows).to.have.length(4)
    expect(rows[1].content).to.equal('Done\n\n::artifact[report.md](record:new){text/markdown|doc-new|new|DOCUMENT|1}')
    expect(rows[3].content).to.equal('Edited\n\n::artifact[report.md](record:new){text/markdown|doc-new|new|DOCUMENT|2}')
    expect(rows[1].parts[0].args).to.equal('{"artifact_id":"new"}')
    expect(rows[1].parts[0].resultPreview).to.equal('saved new')
  })

  it('leaves the original conversation pointing at its own artifact', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)

    await fork(seeded, sinon.stub().resolves({ copies: [copied], failed: [] }))

    const original = store.messagesOf(seeded.source._id) as any[]
    expect(original).to.have.length(6)
    expect(original[1].content).to.contain('record:old')
    expect(original[1].parts[0].args).to.equal('{"artifact_id":"old"}')
    expect(original[5].content).to.equal(`Edited again\n\n${card(3)}`)
  })

  it('does not ask for a copy when no copied message shows an artifact', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const orgId = oid()
    const owner = oid()
    const source = store.addSession({ orgId, userId: owner, initiator: owner, title: 'Plain', sessionType: 'chat' })
    const first = store.addMessage(source, { messageType: 'bot_response', content: 'hello' })
    store.addMessage(source, { messageType: 'bot_response', content: card(1) })
    const copyArtifacts = sinon.stub()

    await fork({ source, forkPoint: first, owner, orgId }, copyArtifacts)

    expect(copyArtifacts.called).to.equal(false)
  })

  it('keeps going when an artifact is gone or no longer readable, leaving its card as it was', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const copyArtifacts = sinon.stub().resolves({ copies: [], failed: [{ artifactId: 'old', reason: 'not_found' }] })

    const result = await fork(seeded, copyArtifacts)

    expect(result.messageCount).to.equal(4)
    expect((store.messagesOf(result.conversation._id)[1] as any).content).to.contain('record:old')
  })

  it('fails before writing anything when an artifact could not be copied', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const sessionsBefore = store.sessions.length
    const copyArtifacts = sinon.stub().resolves({ copies: [], failed: [{ artifactId: 'old', reason: 'error' }] })

    try {
      await fork(seeded, copyArtifacts)
      expect.fail('expected the fork to fail')
    } catch (error) {
      expect((error as { statusCode?: number }).statusCode).to.equal(500)
    }

    expect(store.sessions).to.have.length(sessionsBefore)
    expect(store.writes).to.deep.equal([])
  })

  it('fails before writing anything when the copy service cannot be reached', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const sessionsBefore = store.sessions.length

    try {
      await fork(seeded, sinon.stub().rejects(new Error('connectors down')))
      expect.fail('expected the fork to fail')
    } catch (error) {
      expect((error as Error).message).to.equal('connectors down')
    }

    expect(store.sessions).to.have.length(sessionsBefore)
    expect(store.writes).to.deep.equal([])
  })

  it('copies the artifacts before the fork chat exists, then files the chat under the same id', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const sessionsBefore = store.sessions.length
    const forkId = new Types.ObjectId()
    let sessionsWhenCopying = -1
    const copyArtifacts = sinon.stub().callsFake(async () => {
      sessionsWhenCopying = store.sessions.length
      return { copies: [copied], failed: [] }
    })

    const artifacts = await copyArtifactsForFork(seeded.source, String(seeded.forkPoint._id), forkId, copyArtifacts as never)
    const result = await copySessionUpToMessage(
      seeded.source,
      String(seeded.forkPoint._id),
      String(seeded.owner),
      String(seeded.orgId),
      { sessionType: 'chat' },
      null,
      artifacts,
    )

    expect(sessionsWhenCopying).to.equal(sessionsBefore)
    expect(copyArtifacts.firstCall.args[0]).to.equal(String(forkId))
    expect(String(result.conversation._id)).to.equal(String(forkId))
  })

  it('answers 404, and copies nothing, when the fork point is not a message of the chat', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const seeded = seedWithArtifacts(store)
    const copyArtifacts = sinon.stub()

    try {
      await copyArtifactsForFork(seeded.source, String(new Types.ObjectId()), new Types.ObjectId(), copyArtifacts as never)
      expect.fail('expected a 404')
    } catch (error) {
      expect(error).to.be.instanceOf(NotFoundError)
    }
    expect(copyArtifacts.called).to.equal(false)
  })
})

describe('stripMessageForCopy and runsByAuthor keep a fork inside what its owner may read', () => {
  const OWNER = 'a'.repeat(24)
  const BOB = 'b'.repeat(24)

  it('groups consecutive turns by author, leaving the owner’s own and unstamped turns to the owner', () => {
    const rows = [{ authorUserId: OWNER }, {}, { authorUserId: BOB }, { authorUserId: BOB }, { authorUserId: OWNER }]
    const runs = runsByAuthor(rows, OWNER)
    expect(runs.map((r) => r.author)).to.deep.equal([undefined, BOB, undefined])
    expect(runs.map((r) => r.rows.length)).to.deep.equal([2, 2, 1])
  })

  it('drops the author stamp always, and tools, reasoning, parts and reference data from another author’s turn', () => {
    const message = { content: 'c', authorUserId: BOB, tools: [1], reasoning: [1], parts: [1], referenceData: [1] }
    expect(stripMessageForCopy(message)).to.deep.equal({ content: 'c', tools: [1], reasoning: [1], parts: [1], referenceData: [1] })
    expect(stripMessageForCopy(message, true)).to.deep.equal({ content: 'c' })
  })
})
