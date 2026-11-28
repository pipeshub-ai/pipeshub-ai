import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'
import { buildAgentSharedWithMeFilter } from '../../../../src/libs/repositories/conversation/mongo'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, matchesFilter, oid, plain, settle } from './chat-test-harness'
import { agentListScope, ListScopeEnv, withListScope } from '../helpers/list-scope'

/**
 * An agent's chat list as its query parameters narrow it, through the real list guard and handler over the in-memory
 * store: what main's agent filter builders pinned (`buildAgentConversationFilter`, `buildAgentSharedWithMeFilter`),
 * now made by the guard's list scope and `buildListQuery`. The agent, the org, deletion and archiving scope every
 * list; the owner's chats come with those of a project the caller may open; chats shared with the caller come in
 * their own list, only while shared.
 */

const ORG = oid()
const OTHER_ORG = oid()
const DANA = oid()
const BOB = oid()
const PROJECT = oid()
const OTHER_PROJECT = oid()
const AGENT = 'agent-1'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Row = { title: string }

async function list(
  user: Types.ObjectId,
  query: Record<string, unknown> = {},
  env: ListScopeEnv = { projectIds: [String(PROJECT)] },
): Promise<{ titles: string[]; shared: string[]; error?: unknown }> {
  const handler = withListScope(controller.getAllAgentConversations as JsonHandler, 'agent', agentListScope, env)
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: {}, params: { agentKey: AGENT }, query, body: {}, user: { userId: String(user), orgId: String(ORG) }, context: { requestId: 'req-agent-list' } }
  await handler(req as never, res as never, next as never)
  await settle()
  const body = res.jsonBody as { conversations?: Row[]; sharedWithMeConversations?: Row[] } | undefined
  return {
    titles: (body?.conversations ?? []).map((c) => c.title).sort(),
    shared: (body?.sharedWithMeConversations ?? []).map((c) => c.title).sort(),
    error: next.firstCall?.args[0],
  }
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

const at = (day: string) => new Date(`2024-${day}T12:00:00Z`)

function seed(): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  serveContentSearch(store)
  const agentChat = (fields: Record<string, unknown>) =>
    store.addSession({ orgId: ORG, userId: DANA, initiator: DANA, sessionType: 'agent', agentKey: AGENT, ...fields })
  const own = agentChat({ title: 'march notes', createdAt: at('03-10'), isShared: false })
  store.addMessage(own, { messageType: 'user_query', content: 'where is the needle' })
  agentChat({ title: 'june plan', createdAt: at('06-15'), isShared: true, sharedWith: [{ userId: BOB, accessLevel: 'read' }] })
  agentChat({ title: 'test.special+chars', createdAt: at('07-01') })
  agentChat({ title: 'testXspecialYchars', createdAt: at('07-02') })
  agentChat({ title: 'in project', createdAt: at('08-01'), projectId: PROJECT, projectVisibility: 'project', status: 'Complete' })
  // None of these is on Dana's list of this agent.
  agentChat({ title: 'archived one', createdAt: at('05-01'), isArchived: true })
  agentChat({ title: 'deleted one', createdAt: at('05-02'), isDeleted: true })
  agentChat({ title: 'another agent', agentKey: 'agent-2', createdAt: at('05-03') })
  store.addSession({ orgId: ORG, userId: DANA, initiator: DANA, sessionType: 'chat', title: 'a plain chat', createdAt: at('05-04') })
  store.addSession({ orgId: OTHER_ORG, userId: DANA, initiator: DANA, sessionType: 'agent', agentKey: AGENT, title: 'other org', createdAt: at('05-05') })
  // Bob's chats of the same agent: one shared with Dana, one no longer shared, one in a project.
  store.addSession({
    orgId: ORG, userId: BOB, initiator: BOB, sessionType: 'agent', agentKey: AGENT, title: 'bob shares', createdAt: at('04-01'),
    isShared: true, sharedWith: [{ userId: DANA, accessLevel: 'read' }], status: 'Complete',
  })
  store.addSession({
    orgId: ORG, userId: BOB, initiator: BOB, sessionType: 'agent', agentKey: AGENT, title: 'bob unshared', createdAt: at('04-02'),
    isShared: false, sharedWith: [{ userId: DANA, accessLevel: 'read' }],
  })
  store.addSession({
    orgId: ORG, userId: BOB, initiator: BOB, sessionType: 'agent', agentKey: AGENT, title: 'bob in project', createdAt: at('04-03'),
    projectId: PROJECT, projectVisibility: 'project',
  })
  store.addSession({
    orgId: ORG, userId: BOB, initiator: BOB, sessionType: 'agent', agentKey: AGENT, title: 'bob elsewhere', createdAt: at('04-04'),
    projectId: OTHER_PROJECT, projectVisibility: 'project',
  })
  return store
}

const OWN = ['in project', 'june plan', 'march notes', 'test.special+chars', 'testXspecialYchars']
/** Dana's list: her own, and Bob's chat in the project she may open. */
const DANAS = [...OWN, 'bob in project'].sort()

describe("an agent's chat list and its query parameters", () => {
  afterEach(() => sinon.restore())

  it("lists the caller's chats of this agent in this org, not deleted or archived, and those of a project they may open", async () => {
    seed()
    const out = await list(DANA)
    expect(out.error).to.equal(undefined)
    expect(out.titles).to.deep.equal(DANAS)
    expect((await list(BOB)).titles).to.deep.equal(['bob elsewhere', 'bob in project', 'bob shares', 'bob unshared', 'in project'])
  })

  it('a project branch only for the projects the caller may open; none without them', async () => {
    seed()
    expect((await list(DANA, {}, { projectIds: [] })).titles).to.deep.equal(OWN)
    expect((await list(BOB, {}, { projectIds: [] })).titles).to.deep.equal(['bob elsewhere', 'bob in project', 'bob shares', 'bob unshared'])
  })

  it('a chat shared with the caller is in its own list, only while it is shared; never on their own list', async () => {
    seed()
    const out = await list(DANA)
    expect(out.shared).to.deep.equal(['bob shares'])
    expect(out.titles).to.not.include('bob shares')
  })

  it('the shared list narrows by status', async () => {
    seed()
    expect((await list(DANA, { status: 'Complete' })).shared).to.deep.equal(['bob shares'])
    expect((await list(DANA, { status: 'Failed' })).shared).to.deep.equal([])
  })

  it('a date range keeps the chats created within it; each end alone, or both', async () => {
    seed()
    expect((await list(DANA, { startDate: '2024-06-01T00:00:00Z' })).titles).to.deep.equal(['in project', 'june plan', 'test.special+chars', 'testXspecialYchars'])
    expect((await list(DANA, { endDate: '2024-06-30T00:00:00Z' })).titles).to.deep.equal(['bob in project', 'june plan', 'march notes'])
    expect((await list(DANA, { startDate: '2024-06-01T00:00:00Z', endDate: '2024-07-01T23:00:00Z' })).titles).to.deep.equal(['june plan', 'test.special+chars'])
  })

  it('an unreadable date is a bad request', async () => {
    seed()
    for (const query of [{ startDate: 'bad-date' }, { endDate: 'bad-date' }]) {
      expect((await list(DANA, query)).error, JSON.stringify(query)).to.be.instanceOf(BadRequestError)
    }
  })

  it('shared=true and shared=false narrow by whether the chat is shared', async () => {
    seed()
    expect((await list(DANA, { shared: 'true' })).titles).to.deep.equal(['june plan'])
    expect((await list(DANA, { shared: 'false' })).titles).to.deep.equal(DANAS.filter((t) => t !== 'june plan'))
  })

  it('projectId=unassigned keeps the chats in no project; a project id keeps that project', async () => {
    seed()
    expect((await list(DANA, { projectId: 'unassigned' })).titles).to.deep.equal(OWN.filter((t) => t !== 'in project'))
    expect((await list(DANA, { projectId: String(PROJECT) })).titles).to.deep.equal(['bob in project', 'in project'])
  })

  it("search matches a title literally, its special characters escaped, and the caller's own words in a chat", async () => {
    seed()
    expect((await list(DANA, { search: 'test.special+chars' })).titles).to.deep.equal(['test.special+chars'])
    expect((await list(DANA, { search: 'needle' })).titles).to.deep.equal(['march notes'])
    expect((await list(DANA, { search: 'nothing like it' })).titles).to.deep.equal([])
  })

  it('a search longer than 1000 characters, or given as a list, is a bad request', async () => {
    seed()
    expect((await list(DANA, { search: 'a'.repeat(1001) })).error).to.be.instanceOf(BadRequestError)
    expect((await list(DANA, { search: ['a', 'b'] })).error).to.be.instanceOf(BadRequestError)
  })
})

describe('buildAgentSharedWithMeFilter', () => {
  const q = { orgId: String(ORG), userId: String(DANA), agentKey: AGENT }

  it("is the agent's chats in the caller's org, not deleted or archived, shared with the caller", () => {
    const filter = buildAgentSharedWithMeFilter(q)
    expect(filter).to.include({ sessionType: 'agent', agentKey: AGENT, isDeleted: false, isShared: true, 'sharedWith.userId': String(DANA) })
    expect(String(filter.orgId)).to.equal(String(ORG))
    expect(filter.isArchived).to.deep.equal({ $ne: true })
    expect(filter).to.not.have.property('status')
  })

  it('a status narrows it', () => {
    expect(buildAgentSharedWithMeFilter({ ...q, status: 'Complete' })).to.have.property('status', 'Complete')
  })

  it("matches only a chat shared with the caller, in the caller's org", () => {
    const filter = buildAgentSharedWithMeFilter(q)
    const shared = { sessionType: 'agent', agentKey: AGENT, orgId: ORG, isDeleted: false, isShared: true, sharedWith: [{ userId: String(DANA) }] }
    expect(matchesFilter(shared, filter)).to.equal(true)
    expect(matchesFilter({ ...shared, isShared: false }, filter)).to.equal(false)
    expect(matchesFilter({ ...shared, orgId: OTHER_ORG }, filter)).to.equal(false)
    expect(matchesFilter({ ...shared, isArchived: true }, filter)).to.equal(false)
    expect(matchesFilter({ ...shared, sharedWith: [{ userId: String(BOB) }] }, filter)).to.equal(false)
  })
})
