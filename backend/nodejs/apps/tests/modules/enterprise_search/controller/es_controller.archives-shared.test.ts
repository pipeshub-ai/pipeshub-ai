import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { appendMessages } from '../../../../src/modules/enterprise_search/utils/utils'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * An archived chat shared with other people is shown to them exactly as the live chat is:
 * redacted per viewer, never with the owner's tool output, reasoning or unreadable sources.
 */

const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never
const ORG = oid()
const A = oid() // owner
const C = oid() // reader
const A_CHUNK = 'A-only chunk text about the merger'
const A_TOOL = 'A-TOOL-RESULT'
const UNCITED = 'UNCITED-ANSWER-TEXT'
const READABLE: Record<string, string[]> = { [String(A)]: ['rA', 'rShared'], [String(C)]: ['rShared'] }

type Handler = (req: never, res: never, next: never) => Promise<unknown>

function archivedWorld() {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  ai.replyWith(/\/chat\/records\/access$/, (call) => {
    const viewer = String((jwt.decode((call.headers.authorization ?? '').replace('Bearer ', '')) as { userId: string }).userId)
    return { status: 200, body: { accessible: ((call.body.recordIds as string[]) ?? []).filter((id) => READABLE[viewer]?.includes(id)) } }
  })
  const cite = (chunkIndex: number, recordId: string, content: string) => {
    const doc = new Citation({
      content, chunkIndex, citationType: 'vectordb|document',
      metadata: { orgId: String(ORG), recordId, recordName: `name-${recordId}`, origin: 'UPLOAD', connector: 'KB', webUrl: `https://app/${recordId}` },
    })
    store.citations.push(doc)
    return { citationId: doc._id }
  }
  const chat = store.addSession({
    orgId: ORG, userId: A, initiator: A, title: 'Deal room', sessionType: 'chat', isShared: true,
    sharedWith: [{ userId: C, accessLevel: 'read' }], isArchived: true, archivedBy: A, isDeleted: false,
  })
  store.addMessage(chat, { messageType: 'user_query', content: 'What about the merger?' })
  store.addMessage(chat, {
    messageType: 'bot_response',
    content: 'The merger closes in May [1](https://app/rA).',
    citations: [cite(1, 'rA', A_CHUNK)],
    tools: [{ toolName: 'search', toolResult: A_TOOL }],
    reasoning: [{ content: A_TOOL }],
  })
  store.addMessage(chat, { messageType: 'bot_response', content: UNCITED, citations: [] })
  // Prefetched retrieval of a record only A can read, answered without a citation or a tool call.
  store.addMessage(chat, { messageType: 'bot_response', content: 'PREFETCH-ANSWER', citations: [], sourceRecordIds: ['rA'] })
  store.addMessage(chat, { messageType: 'bot_response', content: 'PURE-MODEL-ANSWER', citations: [], sourceRecordIds: [] })
  return store
}

async function archivesAs(who: Types.ObjectId): Promise<string> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: { authorization: `Bearer t` }, params: {}, body: {}, query: {}, user: { userId: who, orgId: ORG }, context: { requestId: 'r' } }
  await (controller.listAllArchivesConversation(appConfig) as Handler)(req as never, res as never, next as never)
  await settle()
  expect(next.firstCall?.args[0], 'archives error').to.equal(undefined)
  return JSON.stringify(res.jsonBody)
}

describe('GET /conversations/show/archives for a shared viewer', () => {
  afterEach(() => sinon.restore())

  it('redacts the owner’s archived chat for a reader: no source text, tool output, reasoning or uncited answer', async () => {
    archivedWorld()
    const json = await archivesAs(C)
    expect(json).to.include('Deal room')
    for (const leak of [A_CHUNK, A_TOOL, UNCITED, 'PREFETCH-ANSWER', 'name-rA', 'The merger closes']) expect(json, leak).to.not.include(leak)
    expect(json, 'a turn that provably used no sources stays visible').to.include('PURE-MODEL-ANSWER')
    expect(json, 'the stored list is never returned').to.not.include('sourceRecordIds')
    expect(json).to.include('"redacted":true')
  })

  it('shows the owner their own archived chat whole', async () => {
    archivedWorld()
    const json = await archivesAs(A)
    for (const shown of [A_CHUNK, A_TOOL, UNCITED, 'PREFETCH-ANSWER']) expect(json, shown).to.include(shown)
  })
})

describe('what the archived list shows other people about a failed run', () => {
  afterEach(() => sinon.restore())

  it('gives the owner the error details and other viewers none', async () => {
    const store = archivedWorld()
    store.sessions[0]!.set({ failReason: 'FAIL-REASON-TEXT', conversationErrors: [{ message: 'ERROR-TEXT-WITH-RECORD-NAME' }], compactedSummary: 'SUMMARY-TEXT' })
    const asOwner = await archivesAs(A)
    for (const shown of ['FAIL-REASON-TEXT', 'ERROR-TEXT-WITH-RECORD-NAME']) expect(asOwner, shown).to.include(shown)
    const asReader = await archivesAs(C)
    for (const hidden of ['FAIL-REASON-TEXT', 'ERROR-TEXT-WITH-RECORD-NAME', 'SUMMARY-TEXT']) expect(asReader, hidden).to.not.include(hidden)
  })
})

describe('the records a turn drew on are stored with its answer', () => {
  afterEach(() => sinon.restore())

  it('adds the attachments of the question it answers, read from the stored question', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const chat = store.addSession({ orgId: ORG, userId: A, initiator: A, title: 't', sessionType: 'chat' })
    store.addMessage(chat, { messageType: 'user_query', content: 'summarise', attachments: [{ recordId: 'att1' }] })
    await appendMessages(chat._id, ORG, [{ messageType: 'bot_response', content: 'ok', sourceRecordIds: ['r1'] } as never])
    await appendMessages(chat._id, ORG, [{ messageType: 'bot_response', content: 'old answer, no list' } as never])
    const rows = store.messagesOf(chat._id) as Array<Record<string, any>>
    expect(rows[1]!.sourceRecordIds).to.have.members(['r1', 'att1'])
    expect(rows[2]!.sourceRecordIds, 'no recorded list stays unknown').to.equal(undefined)
  })
})
