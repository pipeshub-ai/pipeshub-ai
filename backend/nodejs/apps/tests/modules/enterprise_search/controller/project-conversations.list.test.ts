import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { getProjectConversations, listProjects } from '../../../../src/modules/projects/controller/project.controller'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { FakeSSEResponse, InMemoryChatStore, matchesFilter, oid, plain } from './chat-test-harness'
import { ListScopeEnv, withListScope } from '../helpers/list-scope'

const ORG = oid()
const OWNER = oid()
const READER = oid()
const MEMBER = oid()
const PROJECT = oid()
const OTHER_PROJECT = oid()
const appConfig = { connectorBackend: 'http://connectors', jwtSecret: 'x', scopedJwtSecret: 'y' } as never

type Handler = (req: never, res: never, next: never) => Promise<unknown>
const LIST_OPTIONS = { shareRows: 'extendedOnly' } as const

function seed(): InMemoryChatStore {
  const store = new InMemoryChatStore()
  store.install()
  const base = { orgId: ORG, userId: OWNER, initiator: OWNER, projectId: PROJECT }
  for (const agent of [false, true]) {
    const kind = agent ? { sessionType: 'agent' as const, agentKey: 'agent-1' } : { sessionType: 'chat' as const }
    const tag = agent ? 'agent ' : ''
    store.addSession({ ...base, ...kind, title: `${tag}private` })
    store.addSession({ ...base, ...kind, title: `${tag}visible`, projectVisibility: 'project' })
    store.addSession({ ...base, ...kind, title: `${tag}shared`, isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }] })
  }
  store.addSession({ ...base, sessionType: 'chat', title: 'elsewhere', projectId: OTHER_PROJECT, projectVisibility: 'project' })
  store.addSession({ ...base, sessionType: 'chat', title: 'deleted', projectVisibility: 'project', isDeleted: true })
  return store
}

type Row = { title: string; nextSeq?: number }

async function rows(user: Types.ObjectId, env: ListScopeEnv): Promise<Row[]> {
  const res = new FakeSSEResponse()
  const req = {
    headers: {},
    params: { projectId: String(PROJECT) },
    query: { page: 1, limit: 50 },
    body: {},
    user: { userId: String(user), orgId: String(ORG) },
  }
  const handler = withListScope(getProjectConversations(appConfig) as Handler, 'any', LIST_OPTIONS, env)
  await handler(req as never, res as never, sinon.stub() as never)
  return (res.jsonBody as { conversations: Row[] }).conversations
}

async function projectCardCount(user: Types.ObjectId, env: ListScopeEnv, store: InMemoryChatStore): Promise<number> {
  const chain = { sort: () => chain, skip: () => chain, limit: () => chain, lean: () => chain, exec: async () => [{ _id: PROJECT, orgId: ORG, userId: OWNER, members: [] }] }
  sinon.stub(Project, 'find').returns(chain as never)
  sinon.stub(Project, 'countDocuments').resolves(1 as never)
  sinon.stub(ChatSession, 'aggregate').callsFake((async ([stage]: [{ $match: Record<string, unknown> }]) => {
    const n = store.sessions.filter((s) => matchesFilter(plain(s), stage.$match)).length
    return n ? [{ _id: PROJECT, count: n }] : []
  }) as never)
  const res = new FakeSSEResponse()
  const req = {
    headers: {},
    params: {},
    query: { page: 1, limit: 20, scope: 'all', includeArchived: false },
    body: {},
    user: { userId: String(user), orgId: String(ORG) },
  }
  const handler = withListScope(listProjects(appConfig) as Handler, 'any', LIST_OPTIONS, env)
  await handler(req as never, res as never, sinon.stub() as never)
  return (res.jsonBody as { projects: { conversationCount: number }[] }).projects[0]!.conversationCount
}

async function list(user: Types.ObjectId, env: ListScopeEnv): Promise<string[]> {
  return (await rows(user, env)).map((c) => c.title).sort()
}

describe('project conversation list uses the shared list policy for both kinds', () => {
  let store: InMemoryChatStore
  beforeEach(() => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { teams: [] } } as never)
    sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project: {} } as never)
    store = seed()
  })
  afterEach(() => sinon.restore())

  const env = (_extended: boolean): ListScopeEnv => ({ projectIds: [String(PROJECT)] })

  describe('owner rows plus project-visible rows, chat and agent, nothing shared directly', () => {
    it('the owner sees every live row of the project', async () => {
      expect(await list(OWNER, env(false))).to.deep.equal(['agent private', 'agent shared', 'agent visible', 'private', 'shared', 'visible'])
    })

    it('a project member sees only the project-visible rows', async () => {
      expect(await list(MEMBER, env(false))).to.deep.equal(['agent visible', 'visible'])
    })

    it('a direct recipient sees only the project-visible rows', async () => {
      expect(await list(READER, env(false))).to.deep.equal(['agent visible', 'visible'])
    })
  })

  describe('the project card counts the same rows the list shows', () => {
    for (const [name, user] of [['owner', OWNER], ['project member', MEMBER], ['direct recipient', READER]] as const) {
      it(`for the ${name}`, async () => {
        const shown = (await list(user, env(false))).length
        expect(await projectCardCount(user, env(false), store)).to.equal(shown)
      })
    }

    it('leaves out another member\'s private chat', async () => {
      expect(await projectCardCount(MEMBER, env(false), store)).to.equal(2)
    })
  })
})
