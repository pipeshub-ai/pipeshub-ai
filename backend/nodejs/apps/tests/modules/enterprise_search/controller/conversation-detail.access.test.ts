import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { MongoUserDirectory } from '../../../../src/modules/user_management/services/user-directory.service'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { behindGuard, realGuards } from '../helpers/guarded-chat'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test', jwtSecret: 'x', scopedJwtSecret: 'y' } as never

const ORG = oid()
const OWNER = oid()
const READER = oid()
const TEAM_MEMBER = oid()
const PROJECT_MEMBER = oid()
const STRANGER = oid()
const PROJECT = oid()
const TEAM = 'team-1'
const AGENT_KEY = 'agent-1'

type JsonHandler = (req: never, res: never, next: never) => Promise<unknown>
interface Access {
  isOwner: boolean
  accessLevel: string
  role?: string
  canSend?: boolean
  isCollaborative?: boolean
}
interface Detail {
  conversation: { title: string; access: Access; messages: Array<Record<string, unknown>> }
}

const NAMES = new Map([
  [String(OWNER), 'Olga'],
  [String(READER), 'Rita'],
  [String(PROJECT_MEMBER), ''],
])

function seed() {
  const store = new InMemoryChatStore()
  store.install()
  sinon.stub(MongoUserDirectory.prototype, 'displayNames').callsFake(async (_org, ids) => new Map(ids.map((id) => [id, NAMES.get(id) ?? ''])))
  new FakeAIBackend().install()
  const base = { orgId: ORG, userId: OWNER, initiator: OWNER }
  const withMessages = <T extends { _id: unknown }>(session: T): T => {
    store.addMessage(session as never, { messageType: 'user_query', content: 'q' })
    return session
  }
  const direct = withMessages(store.addSession({ ...base, sessionType: 'chat', title: 'direct', isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }] }))
  const team = withMessages(store.addSession({ ...base, sessionType: 'chat', title: 'team', isShared: true, sharedWith: [{ teamId: TEAM, accessLevel: 'read' }] }))
  const inProject = withMessages(store.addSession({ ...base, sessionType: 'chat', title: 'project', projectId: PROJECT, projectVisibility: 'project' }))
  const archived = withMessages(store.addSession({ ...base, sessionType: 'chat', title: 'archived', isArchived: true }))
  const agent = withMessages(store.addSession({ ...base, sessionType: 'agent', agentKey: AGENT_KEY, title: 'agent', isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }] }))
  sinon.stub(Project, 'findOne').returns({
    lean: () =>
      Promise.resolve({
        orgId: ORG,
        userId: OWNER,
        visibility: 'private',
        members: [{ principalType: 'user', principalId: PROJECT_MEMBER, role: 'viewer' }],
        aclVersion: 0,
      }),
  } as never)
  return {
    direct: String(direct._id),
    team: String(team._id),
    project: String(inProject._id),
    archived: String(archived._id),
    agent: String(agent._id),
  }
}

async function open(
  collab: boolean,
  user: Types.ObjectId,
  params: Record<string, string>,
  options: { teamIds?: string[]; agent?: boolean } = {},
) {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = {
    headers: {},
    params,
    query: {},
    body: {},
    user: { userId: String(user), orgId: String(ORG) },
    context: { requestId: 'req-detail' },
  }
  const handler = options.agent ? (controller.getAgentConversationById as JsonHandler) : (controller.getConversationById(appConfig) as JsonHandler)
  const guarded = behindGuard(realGuards({ collab, teamIds: options.teamIds }), 'read', handler, options.agent ? 'agent' : 'chat')
  await guarded(req as never, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody as Detail, error: next.firstCall?.args[0] as (Error & { statusCode?: number }) | undefined }
}

describe('conversation detail routes follow the guard', () => {
  afterEach(() => sinon.restore())

  describe('flag on', () => {
    it('a reader through a team share opens the chat and gets an access view', async () => {
      const ids = seed()
      const out = await open(true, TEAM_MEMBER, { conversationId: ids.team }, { teamIds: [TEAM] })
      expect(out.error).to.equal(undefined)
      expect(out.body.conversation.title).to.equal('team')
      expect(out.body.conversation.access).to.include({ role: 'read', isOwner: false, accessLevel: 'read', canSend: false })
    })

    it('a project member opens a chat shared with the project', async () => {
      const ids = seed()
      const out = await open(true, PROJECT_MEMBER, { conversationId: ids.project })
      expect(out.error).to.equal(undefined)
      expect(out.body.conversation.access.isCollaborative).to.equal(true)
    })

    it('a team member without the team still gets a 404', async () => {
      const ids = seed()
      const out = await open(true, TEAM_MEMBER, { conversationId: ids.team }, { teamIds: [] })
      expect(out.error?.statusCode).to.equal(404)
    })

    it('an archived chat is still a 404 for its owner', async () => {
      const ids = seed()
      const out = await open(true, OWNER, { conversationId: ids.archived })
      expect(out.error?.statusCode).to.equal(404)
    })

    it('names each turn\'s author and carries seq, the way the feed does', async () => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      sinon.stub(MongoUserDirectory.prototype, 'displayNames').callsFake(async (_org, ids) => new Map(ids.map((id) => [id, NAMES.get(id) ?? ''])))
      const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: 'chat', title: 'shared', isShared: true, sharedWith: [{ userId: READER, accessLevel: 'write' }] })
      store.addMessage(session as never, { messageType: 'user_query', content: 'legacy row, no author' })
      store.addMessage(session as never, { messageType: 'bot_response', content: 'a', requestedBy: OWNER })
      store.addMessage(session as never, { messageType: 'user_query', content: 'from the reader', authorUserId: READER })
      store.addMessage(session as never, { messageType: 'bot_response', content: 'b', requestedBy: READER })
      store.addMessage(session as never, { messageType: 'user_query', content: 'from someone who left', authorUserId: STRANGER })
      const out = await open(true, READER, { conversationId: String(session._id) })
      const rows = out.body.conversation.messages
      expect(rows.map((m) => m.seq)).to.deep.equal([1, 2, 3, 4, 5])
      expect(rows.map((m) => m.author)).to.deep.equal([
        { userId: String(OWNER), displayName: 'Olga' },
        { userId: String(OWNER), displayName: 'Olga' },
        { userId: String(READER), displayName: 'Rita' },
        { userId: String(READER), displayName: 'Rita' },
        { userId: String(STRANGER), displayName: '' },
      ])
    })

    it('emits a note like the feed does: its mentions kept, authored by its writer, legacy notes by the owner', async () => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      sinon.stub(MongoUserDirectory.prototype, 'displayNames').callsFake(async (_org, ids) => new Map(ids.map((id) => [id, NAMES.get(id) ?? ''])))
      const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: 'chat', title: 'shared', isShared: true, sharedWith: [{ userId: READER, accessLevel: 'write' }] })
      store.addMessage(session as never, { messageType: 'note', content: 'from the reader', authorUserId: READER })
      store.addMessage(session as never, { messageType: 'note', content: 'no author stored' })
      const out = await open(true, READER, { conversationId: String(session._id) })
      const rows = out.body.conversation.messages
      expect(rows.map((m) => m.messageType)).to.deep.equal(['note', 'note'])
      expect(rows.map((m) => m.author)).to.deep.equal([
        { userId: String(READER), displayName: 'Rita' },
        { userId: String(OWNER), displayName: 'Olga' },
      ])
    })

    it('the agent detail carries the access view too', async () => {
      const ids = seed()
      const out = await open(true, OWNER, { conversationId: ids.agent, agentKey: AGENT_KEY }, { agent: true })
      expect(out.body.conversation.access).to.include({ role: 'owner', isOwner: true })
    })
  })

  describe('flag off keeps today’s shape', () => {
    it('owner and direct recipient get only isOwner and accessLevel', async () => {
      const ids = seed()
      const owner = await open(false, OWNER, { conversationId: ids.direct })
      expect(owner.body.conversation.access).to.deep.equal({ isOwner: true, accessLevel: 'read' })
      const reader = await open(false, READER, { conversationId: ids.direct })
      expect(reader.body.conversation.access).to.deep.equal({ isOwner: false, accessLevel: 'read' })
    })

    it('a project member reads a project-visible chat', async () => {
      const ids = seed()
      const out = await open(false, PROJECT_MEMBER, { conversationId: ids.project })
      expect(out.error).to.equal(undefined)
      expect(out.body.conversation.access.role).to.equal(undefined)
    })

    it('team shares are ignored, archived chats and strangers are 404', async () => {
      const ids = seed()
      for (const [user, id, teamIds] of [
        [TEAM_MEMBER, ids.team, [TEAM]],
        [OWNER, ids.archived, []],
        [STRANGER, ids.direct, []],
      ] as const) {
        const out = await open(false, user, { conversationId: id }, { teamIds: [...teamIds] })
        expect(out.error?.statusCode, id).to.equal(404)
      }
    })

    it('the agent detail keeps the legacy access shape', async () => {
      const ids = seed()
      const out = await open(false, READER, { conversationId: ids.agent, agentKey: AGENT_KEY }, { agent: true })
      expect(out.error?.statusCode).to.equal(404)
      const owner = await open(false, OWNER, { conversationId: ids.agent, agentKey: AGENT_KEY }, { agent: true })
      expect(owner.body.conversation.access).to.deep.equal({ isOwner: true, accessLevel: 'read' })
    })

    it('adds neither seq nor author to the messages and never reads the directory', async () => {
      const ids = seed()
      const out = await open(false, OWNER, { conversationId: ids.direct })
      const [row] = out.body.conversation.messages
      expect(row).to.not.have.property('seq')
      expect(row).to.not.have.property('author')
      expect((MongoUserDirectory.prototype.displayNames as sinon.SinonStub).called).to.equal(false)
    })
  })
})
