import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { AuthorizationService } from '../../../../../../src/modules/authz/authz.service'
import { ChatAccessLoader } from '../../../../../../src/modules/authz/loaders/chat.loader'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { ChatSession } from '../../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Project } from '../../../../../../src/modules/projects/schema/project.schema'
import {
  ConversationGuards,
  GUARD_MARK,
  guardMarkOf,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { conversationContextOf } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { ConversationOperation } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { OPERATION_REQUIREMENTS } from '../../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'

const oid = () => new Types.ObjectId()
const ORG = oid()
const OWNER = oid()
const B = oid()
const C = oid()
const D = oid()
const TEAM = 'team-1'

type Row = Record<string, unknown>
const user = (id: Types.ObjectId, org: Types.ObjectId = ORG) => ({ userId: id.toString(), orgId: org.toString() })
const direct = (id: Types.ObjectId, accessLevel: 'read' | 'write') => ({ principalType: 'user', userId: id, accessLevel })
const teamRow = (accessLevel: 'read' | 'write') => ({ principalType: 'team', teamId: TEAM, accessLevel })

interface Harness {
  guards: ConversationGuards
  sessions: Row[]
  messages: Row[]
  teams: { callerTeamIds: sinon.SinonStub; exists: sinon.SinonStub }
  users: { displayNames: sinon.SinonStub; findByIds: sinon.SinonStub }
  flags: { isEnabled: sinon.SinonStub }
  clock: FixedClock
  findOne: sinon.SinonStub
  messageFind: sinon.SinonStub
  check: sinon.SinonSpy
  projectFind: sinon.SinonStub
  projectsAccessible: sinon.SinonStub
}

function setup(opts: { collab?: boolean; teamIds?: string[] | 'unresolved'; ownerDisabled?: boolean; ownerMissing?: boolean } = {}): Harness {
  sinon.restore()
  const sessions: Row[] = []
  const messages: Row[] = []
  const findOne = sinon.stub(ChatSession, 'findOne').callsFake(((filter: Row) => {
    const hit =
      sessions.find(
        (s) =>
          String(s._id) === String(filter._id) &&
          String(s.orgId) === String(filter.orgId) &&
          (filter.isDeleted === undefined || (s.isDeleted ?? false) === filter.isDeleted) &&
          (s.sessionType ?? 'chat') === filter.sessionType &&
          (filter.agentKey === undefined || s.agentKey === filter.agentKey),
      ) ?? null
    return { select: () => ({ lean: () => Promise.resolve(hit) }) }
  }) as any)
  const messageFind = sinon.stub(ChatSessionMessage, 'find').callsFake((() => ({
    sort: () => ({ limit: () => ({ select: () => ({ lean: () => Promise.resolve([...messages].sort((a: any, b: any) => b.seq - a.seq)) }) }) }),
  })) as any)
  const projectFind = sinon.stub(Project, 'findOne').returns({ lean: () => Promise.resolve(null) } as any)

  const flags = { isEnabled: sinon.stub().resolves(opts.collab ?? true) }
  const chats = new ChatAccessLoader()
  const authz = new AuthorizationService({ chats, projects: {} as any, flags })
  const check = sinon.spy(authz, 'check')
  const teams = {
    callerTeamIds: sinon.stub().callsFake(() =>
      Promise.resolve(opts.teamIds === 'unresolved' ? { status: 'unresolved' } : { status: 'ok', teamIds: opts.teamIds ?? [] }),
    ),
    exists: sinon.stub().resolves(true),
  }
  const users = {
    displayNames: sinon.stub(),
    findByIds: sinon.stub().callsFake(() =>
      Promise.resolve(opts.ownerMissing ? [] : [{ userId: OWNER.toString(), displayName: 'o', kind: 'human', isDisabled: opts.ownerDisabled === true }]),
    ),
  }
  const projectsAccessible = sinon.stub().resolves([])
  const clock = new FixedClock(1_000)
  const guards = new ConversationGuards({
    authz,
    chats,
    projects: { accessibleProjectIds: projectsAccessible, roleOf: sinon.stub(), assertAtLeast: sinon.stub() },
    flags,
    users,
    teams,
    logger: { warn: sinon.stub() },
    clock,
  })
  return { guards, sessions, messages, teams, users, flags, clock, findOne, messageFind, check, projectFind, projectsAccessible }
}

const session = (over: Row = {}): Row => ({
  _id: oid(),
  orgId: ORG,
  userId: OWNER,
  initiator: OWNER,
  sessionType: 'chat',
  sharedWith: [],
  aclVersion: 1,
  isDeleted: false,
  ...over,
})

const request = (u: { userId: string; orgId: string } | undefined, params: Record<string, string>, extra: Row = {}) =>
  ({ user: u, params, headers: {}, ...extra }) as any

async function run(handler: any, req: any): Promise<unknown> {
  return new Promise((resolve) => {
    void handler(req, {}, (err?: unknown) => resolve(err))
  })
}

const expectDenied = (err: any, status: number, code: string) => {
  expect(err, 'expected next(err)').to.be.an('error')
  expect(err.statusCode).to.equal(status)
  expect(err.code).to.equal(code)
}

describe('ConversationGuards', () => {
  afterEach(() => sinon.restore())

  describe('authorize', () => {
    it('stores a grant for the owner with no team lookup', async () => {
      const h = setup()
      const s = session()
      h.sessions.push(s)
      const req = request(user(OWNER), { conversationId: String(s._id) })
      const err = await run(h.guards.authorize('send', 'chat'), req)
      expect(err).to.equal(undefined)
      const ctx = conversationContextOf(req)
      expect(ctx.grant?.role).to.equal('owner')
      expect(ctx.grant?.session._id.toString()).to.equal(String(s._id))
      expect(ctx.grant?.view).to.deep.include({ isOwner: true, canSend: true })
      expect(h.teams.callerTeamIds.called).to.equal(false)
    })

    it('PH04-03: a caller from another org gets 404, and a malformed id never reaches the PDP', async () => {
      const h = setup()
      const s = session()
      h.sessions.push(s)
      const guard = h.guards.authorize('read', 'chat')
      expectDenied(await run(guard, request(user(OWNER, oid()), { conversationId: String(s._id) })), 404, 'CONVERSATION_NOT_FOUND')
      for (const id of ['nope', '123456789012', '', undefined as any]) {
        expectDenied(await run(guard, request(user(OWNER), { conversationId: id })), 404, 'CONVERSATION_NOT_FOUND')
      }
      expect(h.check.called).to.equal(false)
      expect(h.findOne.callCount).to.equal(1)
    })

    it('authorizeById decides on an id outside the path, as the middleware does', async () => {
      const h = setup()
      const s = session()
      h.sessions.push(s)

      const grant = await h.guards.authorizeById(request(user(OWNER), {}), 'read', 'chat', String(s._id))
      expect(grant.session._id.toString()).to.equal(String(s._id))

      for (const [who, id] of [[user(OWNER, oid()), String(s._id)], [user(OWNER), 'nope'], [user(B), String(s._id)]] as const) {
        const err: any = await h.guards.authorizeById(request(who, {}), 'read', 'chat', id).catch((e) => e)
        expect(err.statusCode, `${who.userId} ${id}`).to.equal(404)
      }
    })

    it('rejects a request with no authenticated user', async () => {
      const h = setup()
      const err: any = await run(h.guards.authorize('read', 'chat'), request(undefined, { conversationId: String(oid()) }))
      expect(err.statusCode).to.equal(401)
    })

    it('PH04-04: an agent session is not found under another agentKey, or on the chat route', async () => {
      const h = setup()
      const s = session({ sessionType: 'agent', agentKey: 'agent-1' })
      h.sessions.push(s)
      const id = String(s._id)
      expect(await run(h.guards.authorize('read', 'agent'), request(user(OWNER), { conversationId: id, agentKey: 'agent-1' }))).to.equal(undefined)
      expectDenied(await run(h.guards.authorize('read', 'agent'), request(user(OWNER), { conversationId: id, agentKey: 'agent-2' })), 404, 'CONVERSATION_NOT_FOUND')
      expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(OWNER), { conversationId: id })), 404, 'CONVERSATION_NOT_FOUND')
      const chat = session()
      h.sessions.push(chat)
      expectDenied(await run(h.guards.authorize('read', 'agent'), request(user(OWNER), { conversationId: String(chat._id), agentKey: 'agent-1' })), 404, 'CONVERSATION_NOT_FOUND')
    })

    it('treats a soft-deleted chat as not found', async () => {
      const h = setup()
      const s = session({ isDeleted: true })
      h.sessions.push(s)
      expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(OWNER), { conversationId: String(s._id) })), 404, 'CONVERSATION_NOT_FOUND')
    })

    it('PH04-05: reads the session once for every op, plus one message read for regenerate', async () => {
      const ops = Object.keys(OPERATION_REQUIREMENTS) as ConversationOperation[]
      for (const op of ops) {
        const h = setup()
        const s = session({ sharedWith: [direct(B, 'write')] })
        h.sessions.push(s)
        await run(h.guards.authorize(op, 'chat'), request(user(OWNER), { conversationId: String(s._id), messageId: String(oid()) }))
        expect(h.findOne.callCount, op).to.equal(1)
        expect(h.messageFind.callCount, op).to.equal(op === 'regenerate' ? 1 : 0)
      }
    })

    it('TM-01: a direct write row never triggers a team lookup, even with a team row present', async () => {
      const h = setup({ teamIds: [TEAM] })
      const s = session({ sharedWith: [direct(B, 'write'), teamRow('read')] })
      h.sessions.push(s)
      const req = request(user(B), { conversationId: String(s._id) })
      expect(await run(h.guards.authorize('send', 'chat'), req)).to.equal(undefined)
      expect(h.teams.callerTeamIds.called).to.equal(false)
      expect(conversationContextOf(req).grant?.role).to.equal('write')
    })

    it('resolves teams once and grants through a team row', async () => {
      const h = setup({ teamIds: [TEAM] })
      const s = session({ sharedWith: [teamRow('write')] })
      h.sessions.push(s)
      const req = request(user(B), { conversationId: String(s._id) })
      expect(await run(h.guards.authorize('send', 'chat'), req)).to.equal(undefined)
      expect(h.teams.callerTeamIds.callCount).to.equal(1)
      expect(conversationContextOf(req).grant?.via.map((p) => p.type)).to.deep.equal(['team'])
    })

    it('a read-via-team caller gets 403 on send', async () => {
      const h = setup({ teamIds: [TEAM] })
      const s = session({ sharedWith: [teamRow('read')] })
      h.sessions.push(s)
      expectDenied(await run(h.guards.authorize('send', 'chat'), request(user(C), { conversationId: String(s._id) })), 403, 'CONVERSATION_READ_ONLY')
    })

    it('TM-04: unresolved teams with only a team row is 503; owner and direct rows are unaffected', async () => {
      const h = setup({ teamIds: 'unresolved' })
      const s = session({ sharedWith: [teamRow('write'), direct(C, 'read')] })
      h.sessions.push(s)
      const id = String(s._id)
      expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(B), { conversationId: id })), 503, 'TEAM_RESOLUTION_UNAVAILABLE')
      expect(await run(h.guards.authorize('read', 'chat'), request(user(OWNER), { conversationId: id }))).to.equal(undefined)
      expect(await run(h.guards.authorize('read', 'chat'), request(user(C), { conversationId: id }))).to.equal(undefined)
      expect(h.teams.callerTeamIds.callCount).to.equal(1)
    })

    it('a stranger on a chat with no team rows is 404 without a team lookup', async () => {
      const h = setup({ teamIds: 'unresolved' })
      const s = session({ sharedWith: [direct(C, 'read')] })
      h.sessions.push(s)
      expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(D), { conversationId: String(s._id) })), 404, 'CONVERSATION_NOT_FOUND')
      expect(h.teams.callerTeamIds.called).to.equal(false)
    })

    it('PDP gate note: unresolved teams with only a project team grant is 503, not 404', async () => {
      const h = setup({ teamIds: 'unresolved' })
      const projectId = oid()
      h.projectFind.returns({
        lean: () =>
          Promise.resolve({
            orgId: ORG,
            userId: oid(),
            visibility: 'private',
            members: [{ principalType: 'team', teamId: TEAM, role: 'viewer' }],
          }),
      } as any)
      const s = session({ projectId, projectVisibility: 'project' })
      h.sessions.push(s)
      expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(B), { conversationId: String(s._id) })), 503, 'TEAM_RESOLUTION_UNAVAILABLE')
    })

    it('never resolves teams for a service account', async () => {
      const h = setup({ teamIds: [TEAM] })
      const s = session({ sharedWith: [teamRow('write')] })
      h.sessions.push(s)
      const err = await run(h.guards.authorize('read', 'chat'), request({ ...user(B), isServiceAccount: true } as any, { conversationId: String(s._id) }))
      expectDenied(err, 404, 'CONVERSATION_NOT_FOUND')
      expect(h.teams.callerTeamIds.called).to.equal(false)
    })

    it('SEC-02: read and write rows for different users do not combine', async () => {
      const h = setup()
      const s = session({ sharedWith: [direct(C, 'read'), direct(B, 'write')] })
      h.sessions.push(s)
      const id = String(s._id)
      expectDenied(await run(h.guards.authorize('send', 'chat'), request(user(C), { conversationId: id })), 403, 'CONVERSATION_READ_ONLY')
      expectDenied(await run(h.guards.authorize('delete', 'chat'), request(user(B), { conversationId: id })), 403, 'CONVERSATION_OWNER_ONLY')
    })

    it('SEC-03: a stranger cannot regenerate on an isShared chat', async () => {
      const h = setup()
      const s = session({ isShared: true })
      h.sessions.push(s)
      const err = await run(h.guards.authorize('regenerate', 'chat'), request(user(D), { conversationId: String(s._id), messageId: String(oid()) }))
      expectDenied(err, 404, 'CONVERSATION_NOT_FOUND')
    })

    describe('regenerate author check', () => {
      const seed = (h: Harness, authorUserId?: Types.ObjectId) => {
        const s = session({ sharedWith: [direct(B, 'write')] })
        h.sessions.push(s)
        const answer = oid()
        h.messages.push(
          { _id: oid(), seq: 1, messageType: 'user_query', ...(authorUserId && { authorUserId }) },
          { _id: answer, seq: 2, messageType: 'bot_response' },
        )
        return request(user(OWNER), { conversationId: String(s._id), messageId: String(answer) })
      }
      const as = (req: any, id: Types.ObjectId) => ({ ...req, user: user(id) })

      it('PH04-06: a legacy question (no authorUserId) belongs to the owner; the editor gets 403', async () => {
        const h = setup()
        const req = seed(h)
        expect(await run(h.guards.authorize('regenerate', 'chat'), req)).to.equal(undefined)
        expectDenied(await run(h.guards.authorize('regenerate', 'chat'), as(req, B)), 403, 'REGENERATE_NOT_ALLOWED')
      })

      it('only the author of the answered question may regenerate, owner included (O-1)', async () => {
        const h = setup()
        const req = seed(h, B)
        expect(await run(h.guards.authorize('regenerate', 'chat'), as(req, B))).to.equal(undefined)
        expectDenied(await run(h.guards.authorize('regenerate', 'chat'), req), 403, 'REGENERATE_NOT_ALLOWED')
      })

      it('a messageId that is not the latest answer falls back to the owner', async () => {
        const h = setup()
        const req = seed(h)
        req.params.messageId = String(oid())
        expectDenied(await run(h.guards.authorize('regenerate', 'chat'), as(req, B)), 403, 'REGENERATE_NOT_ALLOWED')
      })
    })

    describe('D11 owner inactive', () => {
      const editorReq = (h: Harness, op: ConversationOperation) => {
        const s = session({ sharedWith: [direct(B, 'write')] })
        h.sessions.push(s)
        return { guard: h.guards.authorize(op, 'chat'), req: request(user(B), { conversationId: String(s._id), messageId: String(oid()) }) }
      }

      it('PH04-16: send and regenerate are 403 OWNER_INACTIVE, read is unaffected, and the directory is read once per 60 s', async () => {
        const h = setup({ ownerDisabled: true })
        const send = editorReq(h, 'send')
        expectDenied(await run(send.guard, send.req), 403, 'OWNER_INACTIVE')
        const answer = oid()
        h.messages.push({ _id: oid(), seq: 1, messageType: 'user_query', authorUserId: B }, { _id: answer, seq: 2, messageType: 'bot_response' })
        const regen = { guard: h.guards.authorize('regenerate', 'chat'), req: { ...send.req, params: { ...send.req.params, messageId: String(answer) } } }
        expectDenied(await run(regen.guard, regen.req), 403, 'OWNER_INACTIVE')
        const read = { guard: h.guards.authorize('read', 'chat'), req: send.req }
        expect(await run(read.guard, read.req)).to.equal(undefined)
        expect(h.users.findByIds.callCount).to.equal(1)

        h.clock.advance(59_000)
        await run(send.guard, send.req)
        expect(h.users.findByIds.callCount).to.equal(1)
        h.clock.advance(2_000)
        await run(send.guard, send.req)
        expect(h.users.findByIds.callCount).to.equal(2)
      })

      it('a deleted owner (absent from the directory) is inactive', async () => {
        const h = setup({ ownerMissing: true })
        const { guard, req } = editorReq(h, 'send')
        expectDenied(await run(guard, req), 403, 'OWNER_INACTIVE')
      })

      it('a directory failure on a non-owner send is 503 OWNER_STATUS_UNAVAILABLE, before the handler and any SSE header, and is not cached', async () => {
        const h = setup()
        h.users.findByIds.rejects(new Error('db down'))
        const { guard, req } = editorReq(h, 'send')
        const res = { setHeader: sinon.spy(), writeHead: sinon.spy(), flushHeaders: sinon.spy(), write: sinon.spy() }
        const handler = sinon.spy()
        const err = await new Promise<unknown>((resolve) => {
          void guard(req, res as any, (e?: unknown) => {
            if (e === undefined) handler()
            resolve(e)
          })
        })
        expectDenied(err, 503, 'OWNER_STATUS_UNAVAILABLE')
        expect(handler.called).to.equal(false)
        for (const fn of Object.values(res)) expect(fn.called).to.equal(false)
        expect(() => conversationContextOf(req)).to.throw()
        h.users.findByIds.resolves([])
        expectDenied(await run(guard, req), 403, 'OWNER_INACTIVE')
        expect(h.users.findByIds.callCount).to.equal(2)
      })

      it('regenerate also fails closed with the directory down', async () => {
        const h = setup()
        h.users.findByIds.rejects(new Error('db down'))
        const { req } = editorReq(h, 'regenerate')
        const answer = oid()
        h.messages.push({ _id: oid(), seq: 1, messageType: 'user_query', authorUserId: B }, { _id: answer, seq: 2, messageType: 'bot_response' })
        req.params.messageId = String(answer)
        expectDenied(await run(h.guards.authorize('regenerate', 'chat'), req), 503, 'OWNER_STATUS_UNAVAILABLE')
      })

      it('the owner sends and anyone reads with the directory down', async () => {
        const h = setup()
        h.users.findByIds.rejects(new Error('db down'))
        const s = session({ sharedWith: [direct(B, 'write')] })
        h.sessions.push(s)
        const id = String(s._id)
        expect(await run(h.guards.authorize('send', 'chat'), request(user(OWNER), { conversationId: id }))).to.equal(undefined)
        expect(await run(h.guards.authorize('read', 'chat'), request(user(B), { conversationId: id }))).to.equal(undefined)
        expect(h.users.findByIds.called).to.equal(false)
      })

      it('does not look up the owner for the owner, a stranger, or non-send ops', async () => {
        const h = setup({ ownerDisabled: true })
        const s = session({ sharedWith: [direct(B, 'write')] })
        h.sessions.push(s)
        const id = String(s._id)
        await run(h.guards.authorize('send', 'chat'), request(user(OWNER), { conversationId: id }))
        await run(h.guards.authorize('send', 'chat'), request(user(D), { conversationId: id }))
        await run(h.guards.authorize('cancel', 'chat'), request(user(B), { conversationId: id }))
        expect(h.users.findByIds.called).to.equal(false)
      })
    })

    describe('flag off (LEGACY_REQUIREMENTS)', () => {
      const legacy = async (op: ConversationOperation, kind: 'chat' | 'agent', who: Types.ObjectId, over: Row = {}, existing?: Harness) => {
        const h = existing ?? setup({ collab: false, teamIds: [TEAM] })
        const s = session({
          sharedWith: [direct(B, 'write'), direct(C, 'read'), teamRow('write')],
          ...(kind === 'agent' && { sessionType: 'agent', agentKey: 'a1' }),
          ...over,
        })
        h.sessions.push(s)
        const req = request(user(who), { conversationId: String(s._id), ...(kind === 'agent' && { agentKey: 'a1' }) })
        const err = await run(h.guards.authorize(op, kind), req)
        return { h, err, req }
      }

      it('SEC-19: a write recipient behaves as a reader and every denial is 404', async () => {
        for (const op of ['send', 'cancel', 'regenerate', 'rename', 'delete', 'archiveSelf', 'manageCollaborators', 'linkProject'] as const) {
          expectDenied((await legacy(op, 'chat', B)).err, 404, 'CONVERSATION_NOT_FOUND')
        }
        expect((await legacy('read', 'chat', B)).err).to.equal(undefined)
        expect((await legacy('feedback', 'chat', B)).err).to.equal(undefined)
        expect((await legacy('send', 'chat', OWNER)).err).to.equal(undefined)
      })

      it('agent: a direct recipient cannot open or give feedback; the owner can', async () => {
        expectDenied((await legacy('read', 'agent', C)).err, 404, 'CONVERSATION_NOT_FOUND')
        expectDenied((await legacy('feedback', 'agent', C)).err, 404, 'CONVERSATION_NOT_FOUND')
        expect((await legacy('read', 'agent', OWNER)).err).to.equal(undefined)
      })

      it('ignores team rows with no team lookup, and never reads the owner directory or messages', async () => {
        const { h, err } = await legacy('read', 'chat', D)
        expectDenied(err, 404, 'CONVERSATION_NOT_FOUND')
        await legacy('send', 'chat', OWNER, {}, h)
        expect(h.teams.callerTeamIds.called).to.equal(false)
        expect(h.users.findByIds.called).to.equal(false)
        expect(h.messageFind.called).to.equal(false)
      })

      it('a project viewer reads a project-visible chat and agent, but cannot cancel or give chat feedback', async () => {
        const projectId = oid()
        const h = setup({ collab: false })
        h.projectFind.returns({
          lean: () => Promise.resolve({ orgId: ORG, userId: oid(), visibility: 'private', members: [{ principalType: 'user', principalId: D, role: 'viewer' }] }),
        } as any)
        const chat = session({ projectId, projectVisibility: 'project' })
        const agent = session({ projectId, projectVisibility: 'project', sessionType: 'agent', agentKey: 'a1' })
        h.sessions.push(chat, agent)
        const c = (op: ConversationOperation) => run(h.guards.authorize(op, 'chat'), request(user(D), { conversationId: String(chat._id) }))
        const a = (op: ConversationOperation) => run(h.guards.authorize(op, 'agent'), request(user(D), { conversationId: String(agent._id), agentKey: 'a1' }))
        expect(await c('read')).to.equal(undefined)
        expect(await a('read')).to.equal(undefined)
        expectDenied(await c('feedback'), 404, 'CONVERSATION_NOT_FOUND')
        expectDenied(await a('cancel'), 404, 'CONVERSATION_NOT_FOUND')
      })

      it('an unresolvable project team is 404, not 503', async () => {
        const h = setup({ collab: false, teamIds: 'unresolved' })
        h.projectFind.returns({
          lean: () => Promise.resolve({ orgId: ORG, userId: oid(), visibility: 'private', members: [{ principalType: 'team', teamId: TEAM, role: 'viewer' }] }),
        } as any)
        const s = session({ projectId: oid(), projectVisibility: 'project' })
        h.sessions.push(s)
        expectDenied(await run(h.guards.authorize('read', 'chat'), request(user(B), { conversationId: String(s._id) })), 404, 'CONVERSATION_NOT_FOUND')
      })
    })

    it('flows errors to next(err) and never writes to the response', async () => {
      const h = setup()
      h.findOne.throws(new Error('db down'))
      const res: any = { write: sinon.stub(), setHeader: sinon.stub(), writeHead: sinon.stub() }
      const err: any = await new Promise((resolve) => {
        void (h.guards.authorize('read', 'chat') as any)(request(user(OWNER), { conversationId: String(oid()) }), res, resolve)
      })
      expect(err.message).to.equal('db down')
      expect(res.write.called || res.setHeader.called || res.writeHead.called).to.equal(false)
    })
  })

  describe('marks', () => {
    it('tags every handler with what it guards', () => {
      const h = setup()
      expect(guardMarkOf(h.guards.authorize('send', 'chat'))).to.deep.equal({ op: 'send', kind: 'chat' })
      expect(guardMarkOf(h.guards.authorize('read', 'agent'))).to.deep.equal({ op: 'read', kind: 'agent' })
      expect(guardMarkOf(h.guards.listScope('chat'))).to.deep.equal({ list: true, kind: 'chat' })
      expect(guardMarkOf(h.guards.caller())).to.deep.equal({ caller: true })
      expect(guardMarkOf(() => undefined)).to.equal(undefined)
      expect(typeof GUARD_MARK).to.equal('symbol')
    })
  })

  describe('caller', () => {
    it('attaches the caller without a team lookup or a session read', async () => {
      const h = setup()
      const req = request(user(OWNER), {})
      expect(await run(h.guards.caller(), req)).to.equal(undefined)
      expect(conversationContextOf(req).caller).to.deep.equal({ ...user(OWNER), teamIds: 'unresolved' })
      expect(conversationContextOf(req).grant).to.equal(undefined)
      expect(h.teams.callerTeamIds.called || h.findOne.called).to.equal(false)
    })
  })

  describe('listScope', () => {
    it('attaches the caller and an access filter wrapped in $and', async () => {
      const h = setup({ teamIds: [TEAM] })
      const req = request(user(B), {})
      expect(await run(h.guards.listScope('chat'), req)).to.equal(undefined)
      const ctx = conversationContextOf(req)
      expect(ctx.caller.teamIds).to.deep.equal([TEAM])
      expect(ctx.listFilter).to.have.property('$and').with.length(2)
      const clause = (ctx.listFilter as any).$and[0]
      expect(clause.sessionType).to.equal('chat')
      expect(clause.$or).to.have.length(3)
      expect(clause.$or.map((branch: object) => Object.keys(branch)[0])).to.deep.equal(['userId', 'sharedWith', 'sharedWith'])
      expect((ctx.listFilter as any).$and[1]).to.deep.equal({ hiddenFor: { $ne: B } })
    })

    it('honors includeOwned / includeShared', async () => {
      const h = setup()
      const req = request(user(B), {})
      await run(h.guards.listScope('chat', { includeShared: false }), req)
      expect((conversationContextOf(req).listFilter as any).$and[0].$or).to.deep.equal([{ userId: B }])
    })

    it('lists both kinds for kind any, and drops share rows with the flag off when asked', async () => {
      for (const [collab, rows] of [[true, 2], [false, 1]] as const) {
        const h = setup({ collab })
        const req = request(user(B), {})
        await run(h.guards.listScope('any', { shareRows: 'collabOnly' }), req)
        const clause = (conversationContextOf(req).listFilter as any).$and[0]
        expect(clause).to.not.have.property('sessionType')
        expect(clause.$or, `collab ${collab}`).to.have.length(rows)
      }
    })

    it('scopes an agent list by the route agentKey', async () => {
      const h = setup()
      const req = request(user(B), { agentKey: 'a1' })
      await run(h.guards.listScope('agent'), req)
      expect((conversationContextOf(req).listFilter as any).$and[0]).to.deep.include({ sessionType: 'agent', agentKey: 'a1' })
    })

    it('with the flag off ignores team shares but still resolves teams for project access', async () => {
      const h = setup({ collab: false, teamIds: [TEAM] })
      const req = request(user(B), {})
      await run(h.guards.listScope('chat'), req)
      expect(conversationContextOf(req).caller.teamIds).to.deep.equal([])
      expect(JSON.stringify((conversationContextOf(req).listFilter as any).$and[0].$or)).to.not.include('teamId')
      expect(h.teams.callerTeamIds.calledOnce).to.equal(true)
    })

    for (const collab of [true, false]) {
      it(`looks projects up with the caller's teams, flag ${collab ? 'on' : 'off'}`, async () => {
        const h = setup({ collab, teamIds: [TEAM] })
        const req = request(user(B), {})
        await run(h.guards.listScope('chat'), req)
        expect(h.projectsAccessible.firstCall.args[0]).to.deep.include({ userId: B.toString(), teamIds: [TEAM] })
        expect(conversationContextOf(req).accessibleProjectIds).to.deep.equal([])
        expect(conversationContextOf(req).collab).to.equal(collab)
      })
    }

    it('makes no team call and reads no projects when only owned chats are listed', async () => {
      const h = setup({ teamIds: [TEAM] })
      const req = request(user(B), {})
      await run(h.guards.listScope('chat', { includeShared: false }), req)
      expect(h.teams.callerTeamIds.called).to.equal(false)
      expect(h.projectsAccessible.called).to.equal(false)
    })

    it('resolves options per request and can leave project shares out', async () => {
      const h = setup()
      const req = request(user(B), {})
      await run(h.guards.listScope('chat', { includeOwned: () => false, includeProjects: false }), req)
      const or = (conversationContextOf(req).listFilter as any).$and[0].$or
      expect(JSON.stringify(or)).to.not.include('projectVisibility')
      expect(or).to.have.length(1)
      expect(or[0]).to.have.property('sharedWith')
    })

    it('adds the archive scope only with the flag on', async () => {
      for (const collab of [true, false]) {
        const h = setup({ collab })
        const req = request(user(B), {})
        await run(h.guards.listScope('chat', { archived: 'only' }), req)
        const and = (conversationContextOf(req).listFilter as any).$and
        expect(and, `collab ${collab}`).to.have.length(collab ? 3 : 1)
        if (collab) expect(and[1].$or).to.deep.equal([{ isArchived: true, userId: B }, { archivedFor: B }])
      }
    })

    it('LC-21: hides chats the caller left from the list and the shared-with-me list, only with the flag on', async () => {
      for (const collab of [true, false]) {
        const h = setup({ collab, teamIds: [TEAM] })
        const req = request(user(B), { agentKey: 'a1' })
        await run(h.guards.listScope('agent', { shareRows: 'never', sharedWithMeList: true, archived: 'exclude' }), req)
        const ctx = conversationContextOf(req)
        for (const f of [ctx.listFilter, ctx.sharedListFilter]) {
          const hidden = (f as any).$and.filter((c: any) => 'hiddenFor' in c)
          expect(hidden, `collab ${collab}`).to.deep.equal(collab ? [{ hiddenFor: { $ne: B } }] : [])
        }
      }
    })

    it('passes the flag to per-request options', async () => {
      const seen: boolean[] = []
      for (const collab of [true, false]) {
        const h = setup({ collab })
        await run(h.guards.listScope('chat', { includeShared: (_req, flag) => (seen.push(flag), flag) }), request(user(B), {}))
      }
      expect(seen).to.deep.equal([true, false])
    })

    it('builds a shared-with-me filter without owned and project chats, and no share rows in the main filter when asked', async () => {
      const h = setup({ teamIds: [TEAM] })
      const req = request(user(B), { agentKey: 'a1' })
      await run(h.guards.listScope('agent', { shareRows: 'never', sharedWithMeList: true, archived: 'exclude' }), req)
      const ctx = conversationContextOf(req)
      expect(JSON.stringify((ctx.listFilter as any).$and[0].$or)).to.not.include('sharedWith')
      const shared = (ctx.sharedListFilter as any).$and[0].$or
      expect(shared).to.have.length(2)
      expect(shared[0]).to.have.property('sharedWith')
      expect(shared[1]).to.have.property('sharedWith')
    })

    it('fails closed to the user branches when teams are unresolved', async () => {
      const h = setup({ teamIds: 'unresolved' })
      const req = request(user(B), {})
      expect(await run(h.guards.listScope('chat'), req)).to.equal(undefined)
      expect(JSON.stringify((conversationContextOf(req).listFilter as any).$and[0].$or)).to.not.include('teamId')
    })

    it('rejects an unauthenticated list request', async () => {
      const h = setup()
      const err: any = await run(h.guards.listScope('chat'), request(undefined, {}))
      expect(err.statusCode).to.equal(401)
    })
  })
})
