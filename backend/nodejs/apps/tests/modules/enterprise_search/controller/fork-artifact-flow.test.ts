import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import * as conversationArtifacts from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { getConversationRepository } from '../../../../src/libs/repositories/conversation'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * What the fork route does with the artifacts of a chat, from the caller's side:
 * they are copied before the new chat is written, and a fork that fails after
 * the copy takes the copies with it.
 */
const appConfig = { connectorBackend: 'http://connectors.test' } as never
const ORG = oid()
const OWNER = oid()

const card = '::artifact[report.md](record:old){text/markdown|doc-old|old|DOCUMENT|1}'

const reply = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const copied = {
  sourceArtifactId: 'old',
  sourceDocumentId: 'doc-old',
  artifactId: 'new',
  documentId: 'doc-new',
  versions: [{ from: 1, to: 1 }],
}

interface Outcome {
  status: number | undefined
  body: { id?: string } | undefined
  error: Error | undefined
}

describe('fork route: the artifacts of a forked chat', () => {
  let store: InMemoryChatStore
  let fetchStub: sinon.SinonStub
  let discard: sinon.SinonStub
  let chatId: string
  let botMessageId: string

  const seed = (content: string) => {
    store = new InMemoryChatStore()
    store.install()
    const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Report', sessionType: 'chat' })
    store.addMessage(chat, { messageType: 'user_query', content: 'write a report' })
    const bot = store.addMessage(chat, { messageType: 'bot_response', content })
    chatId = String(chat._id)
    botMessageId = String(bot._id)
    store.writes.length = 0
  }

  const fork = async (): Promise<Outcome> => {
    const res = new FakeSSEResponse()
    const next = sinon.stub()
    const req = {
      headers: { authorization: 'Bearer token' },
      params: { conversationId: chatId, messageId: botMessageId },
      body: {},
      query: {},
      user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
      context: { requestId: 'req-fork-artifacts' },
    } as never
    await (controller.forkConversation(appConfig) as (req: never, res: never, next: never) => Promise<unknown>)(
      req,
      res as never,
      next as never,
    )
    await settle()
    return {
      status: res.statusCode,
      body: res.jsonBody as Outcome['body'],
      error: next.firstCall?.args[0] as Error | undefined,
    }
  }

  const copyRequest = () => JSON.parse(String((fetchStub.firstCall.args[1] as RequestInit).body)) as { conversationId: string }

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
    discard = sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves()
  })

  afterEach(() => {
    sinon.restore()
  })

  it('copies them under the id of the fork it then creates, and keeps them', async () => {
    seed(`Done\n\n${card}`)
    let sessionsWhenCopying = -1
    fetchStub.callsFake(async () => {
      sessionsWhenCopying = store.sessions.length
      return reply(200, { copies: [copied], failed: [] })
    })
    const sessionsBefore = store.sessions.length

    const out = await fork()

    expect(out.status).to.equal(201)
    expect(sessionsWhenCopying, 'the chat must not exist while its artifacts are copied').to.equal(sessionsBefore)
    expect(copyRequest().conversationId).to.equal(out.body?.id)
    const rows = store.messagesOf(out.body?.id) as { content: string }[]
    expect(rows[1]!.content).to.contain('record:new')
    expect(discard.called).to.equal(false)
  })

  it('creates no chat, and removes the copies already made, when copying fails part way', async () => {
    seed(`Done\n\n${card}`)
    fetchStub.resolves(reply(500, { detail: 'boom' }))
    const sessionsBefore = store.sessions.length

    const out = await fork()

    expect(out.error).to.be.instanceOf(Error)
    expect(store.sessions).to.have.length(sessionsBefore)
    expect(discard.calledOnce).to.equal(true)
    expect(discard.firstCall.args[1]).to.equal(copyRequest().conversationId)
  })

  it('removes the copies when the chat cannot be written after they were made', async () => {
    seed(`Done\n\n${card}`)
    fetchStub.resolves(reply(200, { copies: [copied], failed: [] }))
    sinon.stub(getConversationRepository(), 'appendMessages').rejects(new Error('mongo down'))
    sinon.stub(ChatSessionMessage, 'deleteMany').resolves({} as never)
    sinon.stub(ChatSession, 'deleteOne').resolves({} as never)

    const out = await fork()

    expect(out.error?.message).to.equal('mongo down')
    expect(discard.calledOnce).to.equal(true)
    expect(discard.firstCall.args[1]).to.equal(copyRequest().conversationId)
  })

  it('still reports the original failure when the copies could not be removed', async () => {
    seed(`Done\n\n${card}`)
    fetchStub.resolves(reply(200, { copies: [copied], failed: [] }))
    sinon.stub(getConversationRepository(), 'appendMessages').rejects(new Error('mongo down'))
    sinon.stub(ChatSessionMessage, 'deleteMany').resolves({} as never)
    sinon.stub(ChatSession, 'deleteOne').resolves({} as never)
    discard.rejects(new Error('connectors down'))

    const out = await fork()

    expect(out.error?.message).to.equal('mongo down')
  })

  it('has nothing to remove when no artifact was copied', async () => {
    seed('Plain answer')
    sinon.stub(getConversationRepository(), 'appendMessages').rejects(new Error('mongo down'))
    sinon.stub(ChatSessionMessage, 'deleteMany').resolves({} as never)
    sinon.stub(ChatSession, 'deleteOne').resolves({} as never)

    const out = await fork()

    expect(out.error?.message).to.equal('mongo down')
    expect(fetchStub.called).to.equal(false)
    expect(discard.called).to.equal(false)
  })

  it('answers 404 and copies nothing for an archived chat', async () => {
    seed(`Done\n\n${card}`)
    ;(store.session(chatId) as { isArchived?: boolean }).isArchived = true
    const sessionsBefore = store.sessions.length

    const out = await fork()

    expect((out.error as { statusCode?: number }).statusCode).to.equal(404)
    expect(store.sessions).to.have.length(sessionsBefore)
    expect(fetchStub.called).to.equal(false)
  })

  it('answers 404 and copies nothing when the message is not in the chat', async () => {
    seed(`Done\n\n${card}`)
    botMessageId = String(new Types.ObjectId())

    const out = await fork()

    expect((out.error as { statusCode?: number }).statusCode).to.equal(404)
    expect(fetchStub.called).to.equal(false)
    expect(discard.called).to.equal(false)
  })
})
