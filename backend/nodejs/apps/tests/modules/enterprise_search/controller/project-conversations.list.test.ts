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
const LIST_OPTIONS = { shareRows: 'collabOnly' } as const

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

type Row = { title: string; unreadCount?: number; collaboratorCount?: number; nextSeq?: number }

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
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { teamIds: [] } } as never)
    sinon.stub(ProjectService, 'assertAccess').resolves({ role: 'viewer', project: {} } as never)
    store = seed()
  })
  afterEach(() => sinon.restore())

  const env = (collab: boolean): ListScopeEnv => ({ collab, projectIds: [String(PROJECT)] })

  describe('flag off: owner rows plus project-visible rows, chat and agent, nothing shared directly', () => {
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

  describe('flag on: direct shares are listed too', () => {
    it('a direct recipient also sees the rows shared with them', async () => {
      expect(await list(READER, env(true))).to.deep.equal(['agent shared', 'agent visible', 'shared', 'visible'])
    })

    it('a project member still does not see private rows', async () => {
      expect(await list(MEMBER, env(true))).to.deep.equal(['agent visible', 'visible'])
    })

    it('SEC-14: only the owner sees who else a listed chat is shared with', async () => {
      const rowsFor = async (user: Types.ObjectId): Promise<string> => {
        const res = new FakeSSEResponse()
        const req = { headers: {}, params: { projectId: String(PROJECT) }, query: { page: 1, limit: 50 }, body: {}, user: { userId: String(user), orgId: String(ORG) } }
        await withListScope(getProjectConversations(appConfig) as Handler, 'any', LIST_OPTIONS, env(true))(req as never, res as never, sinon.stub() as never)
        return JSON.stringify(res.jsonBody)
      }
      expect(await rowsFor(READER)).to.contain('"shared"').and.not.contain('sharedWith')
      expect(await rowsFor(OWNER)).to.contain('sharedWith')
    })
  })

  describe('flag on: shared rows carry the unread count and the owner the collaborator count', () => {
    it('PH06-13: one read-state query for the page; no internal nextSeq in any row', async () => {
      const shared = store.sessions.find((x) => x.title === 'shared')!
      for (let i = 0; i < 3; i += 1) store.addMessage(shared, { messageType: 'user_query', content: `m${i}` })
      store.addReadState({ userId: READER, sessionId: shared._id, lastReadSeq: 1 })

      const recipient = await rows(READER, env(true))
      const row = recipient.find((r) => r.title === 'shared')!
      expect(row.unreadCount).to.equal(2)
      expect(row).to.not.have.property('collaboratorCount')
      expect(recipient.every((r) => !('nextSeq' in r))).to.equal(true)
      expect(store.readStateQueries).to.equal(1)

      const owner = await rows(OWNER, env(true))
      expect(owner.find((r) => r.title === 'shared')).to.include({ unreadCount: 3, collaboratorCount: 1 })
      expect(owner.find((r) => r.title === 'private')).to.not.have.property('unreadCount')
    })

    it('flag off: no unread fields and no read-state query', async () => {
      const off = await rows(OWNER, env(false))
      expect(off.every((r) => !('unreadCount' in r))).to.equal(true)
      expect(store.readStateQueries).to.equal(0)
    })
  })
})
