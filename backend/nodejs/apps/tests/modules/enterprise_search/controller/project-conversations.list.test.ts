import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { getProjectConversations } from '../../../../src/modules/projects/controller/project.controller'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { FakeSSEResponse, InMemoryChatStore, oid } from './chat-test-harness'
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


})
