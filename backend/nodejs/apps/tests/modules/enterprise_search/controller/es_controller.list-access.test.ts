import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import {
  addComputedFields,
  buildFilter,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { EXCLUDE_AGENT, ONLY_AGENT } from '../../../../src/modules/enterprise_search/constants/constants'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'
import { agentArchivesScope, agentListScope, chatArchivesScope, chatListScope, ListScopeEnv, withListScope } from '../helpers/list-scope'

const ORG = oid()
const OWNER = oid()
const READER = oid()
const PMEMBER = oid()
const STRANGER = oid()
const TEAM_USER = oid()
const PROJECT = oid()
const TEAM = 'team-1'
const AGENT_KEY = 'agent-1'
const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'x', scopedJwtSecret: 'y' } as never

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
type Row = Record<string, unknown> & { _id: unknown; title: string; isOwner: boolean; accessLevel: string; access?: Record<string, unknown> }
type Filter = Record<string, unknown>

async function call<T = Record<string, unknown>>(
  handler: JsonHandler,
  user: Types.ObjectId,
  { params = {}, query = {} }: { params?: Record<string, string>; query?: Record<string, string> } = {},
): Promise<{ body: T; error?: Error & { statusCode?: number } }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = { headers: {}, params, query, body: {}, user: { userId: String(user), orgId: String(ORG) }, context: { requestId: 'req-list' } }
  await handler(req as never, res as never, next as never)
  await settle()
  return { body: res.jsonBody as T, error: next.firstCall?.args[0] as never }
}

const titles = (rows: Array<{ title: string }>): string[] => rows.map((r) => r.title).sort()

function seedWorld(): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  new FakeAIBackend().install()
  for (const agent of [false, true]) {
    const kind = agent ? { sessionType: 'agent', agentKey: AGENT_KEY } : {}
    const tag = agent ? 'agent ' : ''
    const base = { orgId: ORG, userId: OWNER, initiator: OWNER, ...kind }
    const shared = { isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }] }
    const inProject = { projectId: PROJECT, projectVisibility: 'project' }
    const archived = { isArchived: true, archivedBy: OWNER }
    store.addSession({ ...base, title: `${tag}own` })
    store.addSession({ ...base, ...shared, title: `${tag}shared` })
    store.addSession({ ...base, ...inProject, title: `${tag}project` })
    store.addSession({ ...base, ...archived, title: `${tag}archived own` })
    store.addSession({ ...base, ...archived, ...shared, title: `${tag}archived shared` })
    store.addSession({ ...base, ...archived, ...inProject, title: `${tag}archived project` })
  }
  return store
}

const ACTORS = [
  { name: 'owner', id: OWNER },
  { name: 'shared reader', id: READER },
  { name: 'project member', id: PMEMBER },
  { name: 'stranger', id: STRANGER },
]
const FLAG_OFF: ListScopeEnv = { collab: false, projectIds: [String(PROJECT)] }

const without = (row: Record<string, unknown>, ...keys: string[]): Record<string, unknown> => {
  const copy = { ...row }
  for (const key of keys) delete copy[key]
  return copy
}

/** What the replaced filters returned for this caller, shaped as the replaced handlers shaped it. */
async function legacy(filter: Filter, userId: Types.ObjectId, stripShared: boolean): Promise<Row[]> {
  const docs = (await ChatSession.find(filter as never).lean().exec()) as unknown as Array<Record<string, unknown>>
  return docs.map((doc) => {
    const row = without(doc, '__v', ...(stripShared ? ['sharedWith'] : []))
    return addComputedFields(row as never, String(userId)) as unknown as Row
  })
}

/** The removed `buildAgentConversationFilter` / `buildAgentSharedWithMeFilter`, with no query params: the flag-off oracle. */
const legacyAgentOwn = (orgId: string, userId: string, agentKey: string, projectIds: Types.ObjectId[] = []): Filter => ({
  ...ONLY_AGENT,
  agentKey,
  orgId: new Types.ObjectId(orgId),
  isDeleted: false,
  $or: [
    { userId: new Types.ObjectId(userId) },
    ...(projectIds.length > 0 ? [{ projectId: { $in: projectIds }, projectVisibility: 'project' }] : []),
  ],
})
const legacyAgentSharedWithMe = (orgId: string, userId: string, agentKey: string): Filter => ({
  ...ONLY_AGENT,
  agentKey,
  orgId: new Types.ObjectId(orgId),
  isDeleted: false,
  isShared: true,
  'sharedWith.userId': userId,
})

const shapeOf = (row: Row, withKeys: boolean): Record<string, unknown> => ({
  ...(withKeys && { keys: Object.keys(row).filter((k) => k !== 'sharedBy' && k !== 'id').sort() }),
  isOwner: row.isOwner,
  accessLevel: row.accessLevel,
})

describe('es_controller list surfaces', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('flag off: same ids and row shape as the filters they replaced', () => {
    const req = (userId: Types.ObjectId, query: Record<string, string> = {}): never =>
      ({ query, params: { agentKey: AGENT_KEY }, user: { userId: String(userId), orgId: String(ORG) } }) as never
    const archived = { isArchived: true, archivedBy: { $exists: true } }
    const projects = [PROJECT]

    // The archive handlers reshape rows themselves (messages, archivedAt); only the access fields are compared there.
    const expectSame = (actual: Row[], expected: Row[], withKeys = true): void => {
      expect(titles(actual)).to.deep.equal(titles(expected))
      const byTitle = (rows: Row[]) => Object.fromEntries(rows.map((r) => [r.title, shapeOf(r, withKeys)]))
      expect(byTitle(actual)).to.deep.equal(byTitle(expected))
    }

    for (const actor of ACTORS) {
      describe(actor.name, () => {
        const org = String(ORG)
        const user = String(actor.id)

        it('GET / source=owned', async () => {
          seedWorld()
          const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)
          const out = await call<{ conversations: Row[] }>(handler, actor.id)
          const filter = { ...buildFilter(req(actor.id), org, user, undefined, true, false), ...EXCLUDE_AGENT }
          expectSame(out.body.conversations, await legacy(filter, actor.id, false))
        })

        it('GET / source=shared', async () => {
          seedWorld()
          const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)
          const out = await call<{ conversations: Row[] }>(handler, actor.id, { query: { source: 'shared' } })
          const filter = { ...buildFilter(req(actor.id), org, user, undefined, false, true, undefined, projects), ...EXCLUDE_AGENT }
          expectSame(out.body.conversations, await legacy(filter, actor.id, true))
        })

        it('GET /show/archives', async () => {
          seedWorld()
          const handler = withListScope(controller.listAllArchivesConversation as JsonHandler, 'chat', chatArchivesScope, FLAG_OFF)
          const out = await call<{ conversations: Row[] }>(handler, actor.id)
          const filter = { ...buildFilter(req(actor.id), org, user), ...archived, ...EXCLUDE_AGENT }
          expectSame(out.body.conversations, await legacy(filter, actor.id, false), false)
        })

        it('GET /:agentKey/conversations, own and shared-with-me', async () => {
          seedWorld()
          const handler = withListScope(controller.getAllAgentConversations as JsonHandler, 'agent', agentListScope, FLAG_OFF)
          const out = await call<{ conversations: Row[]; sharedWithMeConversations: Row[] }>(handler, actor.id, { params: { agentKey: AGENT_KEY } })
          const main = { ...legacyAgentOwn(org, user, AGENT_KEY, projects), isArchived: { $ne: true } }
          const sharedWithMe = { ...legacyAgentSharedWithMe(org, user, AGENT_KEY), isArchived: { $ne: true } }
          expectSame(out.body.conversations, await legacy(main, actor.id, false))
          expectSame(out.body.sharedWithMeConversations, await legacy(sharedWithMe, actor.id, true))
        })

        it('GET /:agentKey/conversations/show/archives', async () => {
          seedWorld()
          const handler = withListScope(controller.listAllArchivesAgentConversation() as JsonHandler, 'agent', agentArchivesScope, FLAG_OFF)
          const out = await call<{ conversations: Row[] }>(handler, actor.id, { params: { agentKey: AGENT_KEY } })
          const filter = { ...legacyAgentOwn(org, user, AGENT_KEY), ...archived }
          expectSame(out.body.conversations, await legacy(filter, actor.id, false), false)
        })

        it('GET /show/archives/search matches archived chats by title, plus own archived agent chats', async () => {
          seedWorld()
          sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
          sinon.stub(ChatSession, 'aggregate').callsFake((async ([stage]: Array<{ $match: Filter }>) =>
            ChatSession.find(stage.$match as never).lean().exec()) as never)
          const handler = withListScope(
            controller.searchArchivedConversations(appConfig) as JsonHandler,
            'chat',
            chatArchivesScope,
            FLAG_OFF,
          )
          const out = await call<{ conversations: Row[] }>(handler, actor.id, { query: { search: 'archived' } })
          const assistant = { ...buildFilter(req(actor.id), org, user), ...archived, ...EXCLUDE_AGENT }
          const expected = [
            ...(await legacy(assistant, actor.id, false)),
            ...(await legacy({ ...legacyAgentOwn(org, user, AGENT_KEY), ...archived }, actor.id, false)).filter(
              (r) => r.isOwner,
            ),
          ].filter((r) => /archived/.test(r.title))
          expect(titles(out.body.conversations)).to.deep.equal(titles(expected))
        })
      })
    }
  })

  describe('team access to a project is the same on detail and in lists', () => {
    for (const collab of [false, true]) {
      it(`a project team member lists the project-visible chat, flag ${collab ? 'on' : 'off'}`, async () => {
        seedWorld()
        sinon.stub(Project, 'find').returns({
          lean: () =>
            Promise.resolve([
              { _id: PROJECT, orgId: ORG, userId: oid(), visibility: 'private', members: [{ principalType: 'team', teamId: TEAM, principalId: TEAM, role: 'viewer' }] },
            ]),
        } as never)
        const env: ListScopeEnv = {
          collab,
          teamIds: [TEAM],
          projectIdsFor: ({ teamIds }) => (Array.isArray(teamIds) && teamIds.includes(TEAM) ? [String(PROJECT)] : []),
        }
        const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, env)
        const member = await call<{ conversations: Row[] }>(handler, TEAM_USER, { query: { source: 'shared' } })
        expect(titles(member.body.conversations)).to.deep.equal(['project'])

        const noTeam = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, { ...env, teamIds: [] })
        const outsider = await call<{ conversations: Row[] }>(noTeam, TEAM_USER, { query: { source: 'shared' } })
        expect(outsider.body.conversations).to.deep.equal([])
      })
    }
  })

  describe('filters stay separate $and members (F-17)', () => {
    it('a search narrows the access filter instead of replacing it', async () => {
      const store = seedWorld()
      store.addSession({ orgId: ORG, userId: STRANGER, initiator: STRANGER, title: 'own but not mine' })
      sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)

      const out = await call<{ conversations: Row[] }>(handler, OWNER, { query: { search: 'own' } })

      expect(titles(out.body.conversations)).to.deep.equal(['own'])
      const filter = (ChatSession.countDocuments as sinon.SinonStub).lastCall.args[0] as { $and: Filter[] }
      expect(Object.keys(filter)).to.deep.equal(['$and'])
      expect(filter.$and).to.have.length(3)
      expect(filter.$and[0]).to.have.nested.property('$and[0].$or')
      expect(filter.$and[1]).to.deep.equal({ isArchived: false })
      expect(filter.$and[2]).to.have.property('$or')
    })

    it('rows of other users that match the search are not listed', async () => {
      seedWorld()
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)
      sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
      const out = await call<{ conversations: Row[] }>(handler, STRANGER, { query: { search: 'own' } })
      expect(out.body.conversations).to.deep.equal([])
    })
  })

  describe('content search is pushed down to the accessible sessions', () => {
    it('finds the one accessible match among more than 10 000 matches of other users', async () => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      const others = Array.from({ length: 10_001 }, () =>
        store.addSession({ orgId: ORG, userId: STRANGER, initiator: STRANGER, title: 'unrelated' }),
      )
      const mine = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'my notes' })
      const matching = [...others, mine].map((s) => ({ sessionId: s._id, content: 'needle in here' }))
      // Groups come back in insertion order and are capped, as in the real pipeline.
      sinon.stub(ChatSessionMessage, 'aggregate').callsFake((async ([{ $match }, , { $limit }]: Array<Record<string, any>>) => {
        const allowed = $match.sessionId ? new Set($match.sessionId.$in.map(String)) : undefined
        return matching
          .filter((m) => !allowed || allowed.has(String(m.sessionId)))
          .slice(0, $limit)
          .map((m) => ({ _id: m.sessionId }))
      }) as never)
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)

      const out = await call<{ conversations: Row[] }>(handler, OWNER, { query: { search: 'needle' } })

      expect(out.body.conversations.map((c) => c.title)).to.deep.equal(['my notes'])
    })
  })

  describe('flag on: rows carry the access view', () => {
    const FLAG_ON: ListScopeEnv = { collab: true, projectIds: [String(PROJECT)] }

    it('shows a write recipient as write, to the recipient and with no other recipients exposed', async () => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      store.addSession({
        orgId: ORG,
        userId: OWNER,
        initiator: OWNER,
        title: 'with editor',
        isShared: true,
        sharedWith: [{ userId: READER, accessLevel: 'write' }],
      })
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_ON)

      const shared = await call<{ conversations: Row[] }>(handler, READER, { query: { source: 'shared' } })
      const owned = await call<{ conversations: Row[] }>(handler, OWNER)

      const [row] = shared.body.conversations
      expect(row?.accessLevel).to.equal('write')
      expect(row?.access).to.deep.include({ role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, isCollaborative: true })
      expect(row).to.not.have.property('sharedWith')
      expect(owned.body.conversations[0]?.access).to.deep.include({ role: 'owner', isOwner: true, canManage: true })
    })

    it('derives the role of a project-inherited row from the project ceiling', async () => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'in project', projectId: PROJECT, projectVisibility: 'project' })
      sinon.stub(Project, 'find').returns({
        lean: () =>
          Promise.resolve([
            {
              _id: PROJECT,
              orgId: ORG,
              userId: oid(),
              visibility: 'private',
              projectChatAccess: 'editor',
              members: [{ principalType: 'user', principalId: PMEMBER, role: 'editor' }],
            },
          ]),
      } as never)
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_ON)

      const out = await call<{ conversations: Row[] }>(handler, PMEMBER, { query: { source: 'shared' } })

      expect(out.body.conversations[0]?.access).to.deep.include({ role: 'write', canSend: true, isOwner: false })
    })

    it('leaves rows without an access object with the flag off', async () => {
      seedWorld()
      const handler = withListScope(controller.getAllConversations as JsonHandler, 'chat', chatListScope, FLAG_OFF)
      const out = await call<{ conversations: Row[] }>(handler, OWNER)
      expect(out.body.conversations.every((c) => !('access' in c))).to.equal(true)
    })
  })

  describe('SEC-14: with the flag on, list rows never show a non-owner the other recipients', () => {
    const FLAG_ON: ListScopeEnv = { collab: true, projectIds: [String(PROJECT)] }
    const OTHER = oid()

    function seedShared(): void {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      sinon.stub(Project, 'find').returns({ lean: () => Promise.resolve([]) } as never)
      const recipients = { isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }, { userId: OTHER, accessLevel: 'write' }] }
      for (const agent of [false, true]) {
        const kind = agent ? { sessionType: 'agent', agentKey: AGENT_KEY } : {}
        const base = { orgId: ORG, userId: OWNER, initiator: OWNER, ...kind, ...recipients }
        store.addSession({ ...base, title: 'archived shared', archivedFor: [OWNER, READER] })
        store.addSession({ ...base, title: 'project shared', projectId: PROJECT, projectVisibility: 'project' })
      }
    }

    const exposes = (rows: Row[]): boolean => JSON.stringify(rows).includes(String(OTHER))

    it('GET /show/archives and /show/archives/search hide them from a recipient, not from the owner', async () => {
      seedShared()
      sinon.stub(ChatSessionMessage, 'aggregate').resolves([])
      sinon.stub(ChatSession, 'aggregate').callsFake((async ([stage]: Array<{ $match: Filter }>) =>
        ChatSession.find(stage.$match as never).lean().exec()) as never)
      const archives = withListScope(controller.listAllArchivesConversation as JsonHandler, 'chat', chatArchivesScope, FLAG_ON)
      const search = withListScope(controller.searchArchivedConversations(appConfig) as JsonHandler, 'chat', chatArchivesScope, FLAG_ON)

      for (const handler of [archives, search]) {
        const reader = await call<{ conversations: Row[] }>(handler, READER, { query: { search: 'archived' } })
        expect(reader.body.conversations.length).to.be.greaterThan(0)
        expect(exposes(reader.body.conversations)).to.equal(false)
        const owner = await call<{ conversations: Row[] }>(handler, OWNER, { query: { search: 'archived' } })
        expect(exposes(owner.body.conversations)).to.equal(true)
      }
    })

    it('GET /:agentKey/conversations hides them on a project-inherited row', async () => {
      seedShared()
      const handler = withListScope(controller.getAllAgentConversations as JsonHandler, 'agent', agentListScope, FLAG_ON)
      const out = await call<{ conversations: Row[]; sharedWithMeConversations: Row[] }>(handler, PMEMBER, { params: { agentKey: AGENT_KEY } })
      expect(titles(out.body.conversations)).to.deep.equal(['project shared'])
      expect(exposes(out.body.conversations)).to.equal(false)
    })
  })
})
