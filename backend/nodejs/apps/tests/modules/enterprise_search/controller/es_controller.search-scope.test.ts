import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import {
  getAllConversationsQuerySchema,
  listAllArchivesConversationQuerySchema,
} from '../../../../src/modules/enterprise_search/validators/es_validators'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, matchesFilter, oid, plain, settle } from './chat-test-harness'
import { chatArchivesScope, chatListScope, ListScopeEnv, withListScope } from '../helpers/list-scope'

/**
 * Searching chats matches message text only in the caller's own chats, and there only the
 * caller's own questions and answers (never tool output): another member's answer, or any
 * message of a chat shared with the caller or visible through a project, can be one hidden from
 * them, and a match on its text tells whether it holds the words searched for. Those chats match
 * by title alone. A search inside one chat of the list (`conversationId` with `search`) is refused.
 */

const ORG = oid()
const DANA = oid()
const BOB = oid()
const PRIYA = oid()
const PROJECT = oid()
const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'x', scopedJwtSecret: 'y' } as never
const SCOPE: ListScopeEnv = { projectIds: [String(PROJECT)] }

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Row = { _id: unknown; title: string }

async function call(
  handler: JsonHandler,
  user: Types.ObjectId,
  query: Record<string, string>,
): Promise<{ titles: string[]; error?: unknown }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: {}, params: {}, query, body: {}, user: { userId: String(user), orgId: String(ORG) }, context: { requestId: 'req-search' } }
  await handler(req as never, res as never, next as never)
  await settle()
  const body = res.jsonBody as { conversations?: Row[] } | undefined
  return { titles: (body?.conversations ?? []).map((c) => c.title).sort(), error: next.firstCall?.args[0] }
}

/** The content match as the server runs it: `$match` over the stored messages, grouped per chat, capped. */
function serveContentSearch(store: InMemoryChatStore): void {
  sinon.stub(ChatSessionMessage, 'aggregate').callsFake((async (pipeline: Array<Record<string, any>>) => {
    const { $match } = pipeline[0] as { $match: Record<string, unknown> }
    const { $limit } = pipeline[2] as { $limit: number }
    const seen = new Set<string>()
    const groups: Array<{ _id: unknown }> = []
    for (const message of store.messages) {
      const row = plain(message)
      if (!matchesFilter(row, $match) || seen.has(String(row.sessionId))) continue
      seen.add(String(row.sessionId))
      groups.push({ _id: row.sessionId })
    }
    return groups.slice(0, $limit)
  }) as never)
}

function seed(): { store: InMemoryChatStore; shared: Types.ObjectId } {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  serveContentSearch(store)
  // The archive search reads both kinds of chat in one pipeline; its first stage is the filter.
  sinon.stub(ChatSession, 'aggregate').callsFake((async (pipeline: Array<{ $match: Record<string, unknown> }>) =>
    ChatSession.find((pipeline[0] as { $match: Record<string, unknown> }).$match as never).lean().exec()) as never)
  const mine = { orgId: ORG, userId: DANA, initiator: DANA }
  const solo = store.addSession({ ...mine, title: 'solo plans' })
  store.addMessage(solo, { messageType: 'user_query', content: 'where is the needle' })
  store.addMessage(solo, { messageType: 'bot_response', content: 'the needle is in R' })
  store.addMessage(solo, { messageType: 'tool_call', content: 'raw tool output: toolneedle' })

  const shared = store.addSession({ ...mine, title: 'board deck', isShared: true, sharedWith: [{ userId: BOB, accessLevel: 'read' }] })
  store.addMessage(shared, { messageType: 'user_query', content: 'summarise R', authorUserId: DANA })
  store.addMessage(shared, { messageType: 'bot_response', content: 'R says the needle figure is 42', requestedBy: DANA })
  // Bob's own turn in Dana's chat: his question and the answer made from his access.
  store.addMessage(shared, { messageType: 'user_query', content: 'and the haystack?', authorUserId: BOB })
  store.addMessage(shared, { messageType: 'bot_response', content: 'the haystack holds 7', requestedBy: BOB })

  const project = store.addSession({ ...mine, title: 'project room', projectId: PROJECT, projectVisibility: 'project' })
  store.addMessage(project, { messageType: 'bot_response', content: 'the project needle', requestedBy: DANA })

  const archived = store.addSession({
    ...mine, title: 'old deck', isArchived: true, archivedBy: DANA, isShared: true, sharedWith: [{ userId: BOB, accessLevel: 'read' }],
  })
  store.addMessage(archived, { messageType: 'bot_response', content: 'an archived needle', requestedBy: DANA })
  return { store, shared: shared._id as Types.ObjectId }
}

const list = () => withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, SCOPE)
const archives = () => withListScope(controller.listAllArchivesConversation(appConfig) as JsonHandler, 'chat', chatArchivesScope, SCOPE)
const archiveSearch = () =>
  withListScope(controller.searchArchivedConversations(appConfig) as JsonHandler, 'chat', chatArchivesScope, SCOPE)

describe('chat search matches message text only in the caller’s own chats', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('a chat shared with Bob matches his search by its title, never by its messages', async () => {
    seed()
    expect((await call(list(), BOB, { source: 'shared', search: 'needle' })).titles).to.deep.equal([])
    expect((await call(list(), BOB, { source: 'shared', search: 'board' })).titles).to.deep.equal(['board deck'])
  })

  it('a chat visible through a project matches by its title only', async () => {
    seed()
    expect((await call(list(), PRIYA, { source: 'shared', search: 'needle' })).titles).to.deep.equal([])
    expect((await call(list(), PRIYA, { source: 'shared', search: 'room' })).titles).to.deep.equal(['project room'])
  })

  it('the owner’s own questions and answers still match', async () => {
    seed()
    expect((await call(list(), DANA, { search: 'needle' })).titles).to.deep.equal(['board deck', 'project room', 'solo plans'])
  })

  it('another member’s turn in the owner’s chat does not match the owner’s search', async () => {
    seed()
    expect((await call(list(), DANA, { search: 'haystack' })).titles).to.deep.equal([])
  })

  it('tool output never matches', async () => {
    seed()
    expect((await call(list(), DANA, { search: 'toolneedle' })).titles).to.deep.equal([])
  })

  it('the one-chat list form tells Bob nothing about a message', async () => {
    const { shared } = seed()
    const out = await call(list(), BOB, { source: 'shared', conversationId: String(shared), search: 'needle figure is 42' })
    expect(out.titles).to.deep.equal([])
    expect((await call(list(), BOB, { source: 'shared', conversationId: String(shared) })).titles).to.deep.equal(['board deck'])
  })

  it('archived chats shared with Bob match his archive search by title only', async () => {
    seed()
    expect((await call(archiveSearch(), BOB, { search: 'needle' })).titles).to.deep.equal([])
    expect((await call(archiveSearch(), BOB, { search: 'old' })).titles).to.deep.equal(['old deck'])
    expect((await call(archives(), BOB, { search: 'needle' })).titles).to.deep.equal([])
  })

  it('the owner’s archive search still matches her own words', async () => {
    seed()
    expect((await call(archiveSearch(), DANA, { search: 'needle' })).titles).to.deep.equal(['old deck'])
  })
})

describe('a search inside one chat of a list is refused', () => {
  const id = String(oid())

  for (const [name, schema] of [
    ['GET /conversations', getAllConversationsQuerySchema],
    ['GET /conversations/show/archives', listAllArchivesConversationQuerySchema],
  ] as const) {
    it(`${name} refuses conversationId with search`, () => {
      expect(schema.safeParse({ query: { conversationId: id, search: 'needle' } }).success).to.equal(false)
    })

    it(`${name} takes each one alone`, () => {
      expect(schema.safeParse({ query: { conversationId: id } }).success).to.equal(true)
      expect(schema.safeParse({ query: { search: 'needle' } }).success).to.equal(true)
    })
  }
})
