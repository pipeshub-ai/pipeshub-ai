import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as artifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'
import {
  TEMPORARY_CHAT_ACTION_ERROR,
  TEMPORARY_CHAT_TITLE,
  TEMPORARY_CHAT_TTL_MS,
} from '../../../../src/modules/enterprise_search/utils/temporary-chat'
import { getConversationRepository } from '../../../../src/libs/repositories/conversation'
import {
  sweepExpiredTemporaryChats,
  TtlSweeper,
} from '../../../../src/libs/services/ttl-sweeper.service'
import { buildSessionFilter } from '../../../../src/libs/repositories/conversation/mongo'
import { FakeAIBackend } from './chat-test-harness'
import { flows, startStream } from './streaming-flows'

const appConfig = {
  aiBackend: 'http://ai.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const ORG = oid()
const OWNER = oid()
const OTHER = oid()

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>

interface Outcome<T> {
  status: number | undefined
  body: T
  error: (Error & { statusCode?: number }) | undefined
}

async function call<T = Record<string, unknown>>(
  handler: JsonHandler,
  {
    params = {},
    query = {},
    body = {},
    user = { userId: OWNER, orgId: ORG },
  }: {
    params?: Record<string, string>
    query?: Record<string, string>
    body?: Record<string, unknown>
    user?: { userId: Types.ObjectId; orgId: Types.ObjectId }
  } = {},
): Promise<Outcome<T>> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = {
    headers: { authorization: 'Bearer token' },
    params,
    query,
    body,
    user,
    context: { requestId: 'req-temporary' },
  }
  await handler(req as never, res as never, next as never)
  await settle()
  return {
    status: res.statusCode,
    body: res.jsonBody as T,
    error: next.firstCall?.args[0] as Outcome<T>['error'],
  }
}

function fresh(): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  return store
}

describe('es_controller temporary chat', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('buildSessionFilter hides temporary rows unless looking up that one id', () => {
    const base = {
      orgId: String(ORG),
      userId: String(OWNER),
      scope: { kind: 'chat' as const },
      owned: true,
      shared: true,
      archived: 'no' as const,
      sort: {},
      skip: 0,
      limit: 20,
    }
    expect(buildSessionFilter(base).isTemporary).to.deep.equal({ $ne: true })
    expect(buildSessionFilter({ ...base, id: String(oid()) }).isTemporary).to.deep.equal({
      $ne: true,
    })
    expect(buildSessionFilter({ ...base, includeTemporary: true }).isTemporary).to.deep.equal({
      $ne: true,
    })
    expect(
      buildSessionFilter({ ...base, id: String(oid()), includeTemporary: true }).isTemporary,
    ).to.equal(undefined)
  })

  it('getAllConversations excludes temporary rows and keeps a normal chat', async () => {
    const store = fresh()
    store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'kept',
      lastActivityAt: 2,
    })
    store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'hidden',
      isTemporary: true,
      lastActivityAt: 3,
    })

    const out = await call<{ conversations: Array<{ title: string }> }>(
      controller.getAllConversations as JsonHandler,
    )

    expect(out.error).to.equal(undefined)
    expect(out.body.conversations.map((c) => c.title)).to.deep.equal(['kept'])
  })

  it('list archives excludes temporary rows', async () => {
    const store = fresh()
    store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'archived kept',
      isArchived: true,
      archivedBy: OWNER,
    })
    store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'archived temporary',
      isArchived: true,
      archivedBy: OWNER,
      isTemporary: true,
    })

    const out = await call<{ conversations: Array<{ title: string }> }>(
      controller.listAllArchivesConversation(appConfig) as JsonHandler,
    )

    expect(out.error).to.equal(undefined)
    expect(out.body.conversations.map((c) => c.title)).to.deep.equal([
      'archived kept',
    ])
  })

  it('getConversationById returns a temporary chat and its messages to the owner', async () => {
    const store = fresh()
    const chat = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'temp',
      isTemporary: true,
    })
    store.addMessage(chat, { messageType: 'user_query', content: 'secret payroll' })
    store.addMessage(chat, { messageType: 'bot_response', content: 'ok' })

    const out = await call<{
      conversation: { messages: Array<{ content: string }> }
    }>(controller.getConversationById(appConfig) as JsonHandler, {
      params: { conversationId: String(chat._id) },
    })

    expect(out.error).to.equal(undefined)
    expect(out.body.conversation.messages.map((m) => m.content)).to.deep.equal([
      'secret payroll',
      'ok',
    ])
  })

  it('getConversationById hides a temporary chat from someone else', async () => {
    const store = fresh()
    const chat = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'temp',
      isTemporary: true,
    })

    const out = await call(controller.getConversationById(appConfig) as JsonHandler, {
      params: { conversationId: String(chat._id) },
      user: { userId: OTHER, orgId: ORG },
    })

    expect(out.error?.statusCode).to.equal(404)
  })

  it('share, pin, archive, rename, fork and project-link return 400 for a temporary chat', async () => {
    const store = fresh()
    const chat = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'temp',
      isTemporary: true,
    })
    const params = { conversationId: String(chat._id) }

    const share = await call(controller.shareConversationById(appConfig) as JsonHandler, {
      params,
      body: { userIds: [String(OTHER)] },
    })
    const pin = await call(controller.pinConversation as JsonHandler, { params })
    const archive = await call(controller.archiveConversation as JsonHandler, { params })
    const rename = await call(controller.updateTitle as JsonHandler, {
      params,
      body: { title: 'nope' },
    })
    const fork = await call(controller.forkConversation(appConfig) as JsonHandler, {
      params: { ...params, messageId: String(oid()) },
    })
    const projectLink = await call(controller.setConversationProject as JsonHandler, {
      params,
      body: { projectId: String(oid()) },
    })

    for (const out of [share, pin, archive, rename, fork, projectLink]) {
      expect(out.error?.statusCode).to.equal(400)
      expect(out.error?.message).to.equal(TEMPORARY_CHAT_ACTION_ERROR)
    }
  })

  it('purge deletes the temporary row and its messages, and leaves a normal chat', async () => {
    const store = fresh()
    sinon.stub(artifacts, 'deleteConversationArtifacts').resolves()
    const temp = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'temp',
      isTemporary: true,
    })
    store.addMessage(temp, { messageType: 'user_query', content: 'hi' })
    const kept = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'kept',
    })

    const purged = await call(controller.purgeTemporaryConversation(appConfig) as JsonHandler, {
      params: { conversationId: String(temp._id) },
    })
    expect(purged.error).to.equal(undefined)
    expect(purged.status).to.equal(204)
    expect(store.session(temp._id)).to.equal(undefined)
    expect(store.messagesOf(temp._id)).to.deep.equal([])
    expect(store.session(kept._id)?.title).to.equal('kept')

    const refused = await call(controller.purgeTemporaryConversation(appConfig) as JsonHandler, {
      params: { conversationId: String(kept._id) },
    })
    expect(refused.error?.statusCode).to.equal(400)
    expect(store.session(kept._id)?.title).to.equal('kept')
  })

  it('the sweep deletes only expired temporary rows', async () => {
    const store = fresh()
    const expired = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'expired',
      isTemporary: true,
      expiresAt: new Date(Date.now() - 1000),
    })
    const live = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'live',
      isTemporary: true,
      expiresAt: new Date(Date.now() + 60_000),
    })
    const normal = store.addSession({
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 'normal',
      expiresAt: new Date(Date.now() - 1000),
    })

    const repo = getConversationRepository()
    const due = await repo.listExpiredTemporary(new Date(), 50)
    expect(due.map((r) => r.id)).to.deep.equal([String(expired._id)])
    expect(due[0]?.userId).to.equal(String(OWNER))

    const deleted = await repo.hardDeleteTemporary(String(expired._id), {
      orgId: String(ORG),
      userId: String(OWNER),
    })
    expect(deleted?.session._id).to.equal(String(expired._id))
    expect(store.session(expired._id)).to.equal(undefined)
    expect(store.session(live._id)?.title).to.equal('live')
    expect(store.session(normal._id)?.title).to.equal('normal')
  })

  it('sweepExpiredTemporaryChats no-ops when the conversation store is disconnected', async () => {
    const repo = getConversationRepository()
    sinon.stub(repo, 'isConnected').returns(false)
    expect(await sweepExpiredTemporaryChats(new Date())).to.equal(0)
  })

  it('TtlSweeper start and stop run without a document store', async () => {
    const sweeper = new TtlSweeper(
      { info() {}, error() {} } as never,
      60 * 60 * 1000,
    )
    sweeper.start()
    sweeper.start()
    await sweeper.stop()
  })

  it('sweepExpiredTemporaryChats deletes expired rows when the store is connected', async () => {
    const repo = getConversationRepository()
    sinon.stub(repo, 'isConnected').returns(true)
    sinon.stub(repo, 'listExpiredTemporary').resolves([
      { id: 's1', orgId: String(ORG), userId: String(OWNER) },
    ])
    const del = sinon
      .stub(repo, 'hardDeleteTemporary')
      .resolves({ session: { _id: 's1' } } as never)
    expect(await sweepExpiredTemporaryChats(new Date())).to.equal(1)
    expect(del.calledOnce).to.equal(true)
  })

  it('streamChat with temporary: true flags the session and does not use the query as the title', async () => {
    const streamChat = flows.find((f) => f.name === 'streamChat')
    if (!streamChat) throw new Error('streamChat flow missing')
    const before = Date.now()
    const run = await startStream(
      {
        ...streamChat,
        prepare: () => ({
          params: {},
          body: { query: 'secret payroll numbers', temporary: true },
        }),
      },
      undefined,
      undefined,
      { wait: false },
    )
    await settle()
    const session = run.conversation()
    expect(session.isTemporary).to.equal(true)
    expect(session.title).to.equal(TEMPORARY_CHAT_TITLE)
    const expiresAt = session.expiresAt as Date
    expect(expiresAt).to.be.instanceOf(Date)
    expect(expiresAt.getTime()).to.be.at.least(before + TEMPORARY_CHAT_TTL_MS - 1000)
    expect(expiresAt.getTime()).to.be.at.most(Date.now() + TEMPORARY_CHAT_TTL_MS + 1000)
    run.res.emit('close')
    await run.done.catch(() => undefined)
  })

  it('streamChat with temporary: true and a projectId is refused before anything is saved', async () => {
    const store = fresh()
    new FakeAIBackend().install()
    const res = new FakeSSEResponse()
    let error: (Error & { statusCode?: number }) | undefined
    try {
      await controller.streamChat(appConfig)(
        {
          headers: { authorization: 'Bearer token' },
          params: {},
          query: {},
          body: {
            query: 'hello',
            temporary: true,
            projectId: String(oid()),
          },
          user: { userId: OWNER, orgId: ORG },
          context: { requestId: 'req-temporary' },
        } as never,
        res as never,
      )
    } catch (e) {
      error = e as typeof error
    }
    expect(error?.statusCode).to.equal(400)
    expect(error?.message).to.equal('Temporary chats cannot be added to a project')
    expect(store.sessions).to.have.length(0)
  })

  it('streamChat without temporary leaves a normal chat', async () => {
    const streamChat = flows.find((f) => f.name === 'streamChat')
    if (!streamChat) throw new Error('streamChat flow missing')
    const run = await startStream(streamChat, undefined, undefined, {
      wait: false,
    })
    await settle()
    const session = run.conversation()
    expect(session.isTemporary).to.not.equal(true)
    expect(session.title).to.equal('What changed in the release?')
    run.res.emit('close')
    await run.done.catch(() => undefined)
  })
})
