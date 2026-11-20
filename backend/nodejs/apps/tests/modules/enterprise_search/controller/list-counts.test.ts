import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { FakeAIBackend, FakeQuery, FakeSSEResponse, InMemoryChatStore, matchesFilter, oid, plain, settle } from './chat-test-harness'
import { agentListScope, chatListScope, withListScope } from '../helpers/list-scope'
import { testAcl } from '../helpers/test-extensions'
import { useConversationExtensions } from '../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'

const ORG = oid()
const OWNER = oid()
const OTHER = oid()
const AGENT_KEY = 'agent-1'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Pagination = { page: number; limit: number; totalCount: number; totalPages: number; hasNextPage: boolean; hasPrevPage: boolean }
type ListBody = { conversations: Array<{ title: string }>; sharedWithMeConversations?: Array<{ title: string }>; pagination: Pagination }

const chatList = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope)
const agentList = withListScope(controller.getAllAgentConversations as JsonHandler, 'agent', agentListScope)

async function call(handler: JsonHandler, query: Record<string, string>, params: Record<string, string> = {}): Promise<ListBody> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: {}, params, query, body: {}, user: { userId: String(OWNER), orgId: String(ORG) }, context: { requestId: 'req-counts' } }
  await handler(req as never, res as never, next as never)
  await settle()
  expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  return res.jsonBody as ListBody
}

/** The store, with counts that honour `limit` as Mongo does. */
function seed(sessions: number, over: Record<string, unknown> = {}): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  ;(ChatSession.countDocuments as unknown as sinon.SinonStub).callsFake(((filter: never, options?: { limit?: number }) =>
    new FakeQuery(() => Math.min(store.sessions.filter((s) => matchesFilter(plain(s), filter)).length, options?.limit ?? Infinity))) as never)
  for (let i = 1; i <= sessions; i += 1) {
    store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: `mine ${String(i)}`, lastActivityAt: i, ...over })
  }
  return store
}

const countOptions = (): unknown => (ChatSession.countDocuments as unknown as sinon.SinonStub).lastCall.args[1]

describe('list paging and capped counts', () => {
  beforeEach(() => useConversationExtensions({ acl: testAcl }))
  afterEach(() => {
    useConversationExtensions(undefined)
    sinon.restore()
  })

  it('#68: the count stops at 1,000, and the page after the cap is still known to exist', async () => {
    seed(1001)

    const tenth = await call(chatList, { page: '10', limit: '100' })
    expect(tenth.conversations).to.have.length(100)
    expect(tenth.pagination).to.include({ totalCount: 1000, totalPages: 10, hasNextPage: true, hasPrevPage: true })
    expect(countOptions()).to.deep.equal({ limit: 1000 })

    const last = await call(chatList, { page: '11', limit: '100' })
    expect(last.conversations.map((c) => c.title)).to.deep.equal(['mine 1'])
    expect(last.pagination.hasNextPage).to.equal(false)
  })

  it('a page that ends exactly at the last row has no next page', async () => {
    seed(4)
    const first = await call(chatList, { page: '1', limit: '2' })
    const second = await call(chatList, { page: '2', limit: '2' })
    expect(first.conversations.map((c) => c.title)).to.deep.equal(['mine 4', 'mine 3'])
    expect(first.pagination).to.include({ totalCount: 4, hasNextPage: true })
    expect(second.conversations.map((c) => c.title)).to.deep.equal(['mine 2', 'mine 1'])
    expect(second.pagination).to.include({ totalCount: 4, hasNextPage: false })
  })

  it('source=shared pages the same way', async () => {
    const store = seed(0)
    for (let i = 1; i <= 3; i += 1) {
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, title: `theirs ${String(i)}`, lastActivityAt: i, isShared: true, sharedWith: [{ userId: OWNER, accessLevel: 'read' }] })
    }
    const first = await call(chatList, { source: 'shared', page: '1', limit: '2' })
    expect(first.conversations.map((c) => c.title)).to.deep.equal(['theirs 3', 'theirs 2'])
    expect(first.pagination).to.include({ totalCount: 3, hasNextPage: true })
    expect(countOptions()).to.deep.equal({ limit: 1000 })
    const second = await call(chatList, { source: 'shared', page: '2', limit: '2' })
    expect(second.pagination).to.include({ totalCount: 3, hasNextPage: false })
  })

  it('agent lists: the count stops at 1,000, the next page comes from one extra row, and each list keeps to the page size', async () => {
    const store = seed(1001, { sessionType: 'agent', agentKey: AGENT_KEY })
    for (let i = 1; i <= 3; i += 1) {
      store.addSession({ orgId: ORG, userId: OTHER, initiator: OTHER, sessionType: 'agent', agentKey: AGENT_KEY, title: `theirs ${String(i)}`, lastActivityAt: i, isShared: true, sharedWith: [{ userId: OWNER, accessLevel: 'read' }] })
    }

    const tenth = await call(agentList, { page: '10', limit: '100' }, { agentKey: AGENT_KEY })
    expect(tenth.conversations).to.have.length(100)
    expect(tenth.pagination).to.include({ totalCount: 1000, hasNextPage: true })
    expect(countOptions()).to.deep.equal({ limit: 1000 })

    const first = await call(agentList, { page: '1', limit: '2' }, { agentKey: AGENT_KEY })
    expect(first.conversations.map((c) => c.title)).to.deep.equal(['mine 1001', 'mine 1000'])
    expect(first.sharedWithMeConversations?.map((c) => c.title)).to.deep.equal(['theirs 3', 'theirs 2'])

    const last = await call(agentList, { page: '11', limit: '100' }, { agentKey: AGENT_KEY })
    expect(last.conversations).to.have.length(1)
    expect(last.pagination.hasNextPage).to.equal(false)
  })

  it('an unknown conversation id lists nothing and has no next page', async () => {
    seed(2)
    const out = await call(chatList, { conversationId: String(new Types.ObjectId()) })
    expect(out.conversations).to.deep.equal([])
    expect(out.pagination).to.include({ totalCount: 0, hasNextPage: false })
  })
})
