import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import http from 'http'
import { AddressInfo } from 'net'
import jwt from 'jsonwebtoken'
import { Container } from 'inversify'
import { Types } from 'mongoose'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { JwtServiceTokenIssuer } from '../../../src/libs/services/service-token.issuer'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { AuthorizationService } from '../../../src/modules/authz/authz.service'
import { AuthzController } from '../../../src/modules/authz/authz.controller'
import { AccessPreviewService, PREVIEW_PRINCIPAL_CAP } from '../../../src/modules/authz/access-preview.service'
import { ExplainService } from '../../../src/modules/authz/explain.service'
import { createAuthzRouter } from '../../../src/modules/authz/routes/authz.routes'
import { SubjectTeamResolver } from '../../../src/modules/authz/subject-team.resolver'
import { IChatAccessLoader, IScopedChatLoader, LoadedChat, ScopedLoadedChat, ScopedSession } from '../../../src/modules/authz/ports'
import { projectFactsOf } from '../../../src/modules/authz/loaders/project.loader'
import { IProjectAccessPort } from '../../../src/modules/authz/ports/project-access.port'
import { COLLAB_TYPES } from '../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { ConversationGuards } from '../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { projectRoleFor } from '../../../src/modules/projects/services/project-access.adapter'
import { IProjectDocument } from '../../../src/modules/projects/types/project.interfaces'
import { ITeamDirectory, TeamIdsResult } from '../../../src/modules/user_management/services/team-directory.service'
import { CallerIdentity } from '../../../src/libs/types/caller-identity'
import { headerAuth } from '../enterprise_search/helpers/serve-routers'

const oid = (): string => new Types.ObjectId().toString()
const ORG = oid()
const [A, B, C, D, ADMIN, PM, PM2] = [oid(), oid(), oid(), oid(), oid(), oid(), oid()]
const T_SALES = 'team-sales'
const T_OPS = 'team-ops'
const SCOPED_SECRET = 'scoped-secret-for-tests'

interface Call {
  userId: string
  authorization: string | undefined
}

class FakeTeams implements ITeamDirectory {
  readonly calls: Call[] = []
  readonly byUser = new Map<string, string[]>()
  down = false
  async callerTeamIds(identity: CallerIdentity): Promise<TeamIdsResult> {
    const headers = identity.authHeaders
    this.calls.push({ userId: identity.userId, authorization: headers.Authorization ?? headers.authorization })
    return this.down ? { status: 'unresolved' } : { status: 'ok', teamIds: this.byUser.get(identity.userId) ?? [] }
  }
  async teamsVersion(): Promise<number> {
    return 0
  }
  async exists(): Promise<boolean> {
    return true
  }
  async memberUserIds(): Promise<{ status: 'unresolved' }> {
    return { status: 'unresolved' }
  }
}

interface ChatSeed {
  id: string
  owner?: string
  sharedWith?: Array<{ userId?: string; teamId?: string; accessLevel: 'read' | 'write' }>
  projectId?: string
  projectVisibility?: 'private' | 'project'
}

type Member = { principalType: 'user'; principalId: Types.ObjectId; role: 'viewer' | 'editor' } | { principalType: 'team'; teamId: string; role: 'viewer' | 'editor' }

const user = (id: string, role: 'viewer' | 'editor' = 'viewer'): Member => ({ principalType: 'user', principalId: new Types.ObjectId(id), role })
const team = (teamId: string, role: 'viewer' | 'editor' = 'viewer'): Member => ({ principalType: 'team', teamId, role })

const projectDoc = (id: string, owner: string, members: Member[], over: Record<string, unknown> = {}): IProjectDocument =>
  ({
    _id: new Types.ObjectId(id),
    orgId: new Types.ObjectId(ORG),
    userId: new Types.ObjectId(owner),
    visibility: 'private',
    chatSharing: 'private',
    projectChatAccess: 'viewer',
    aclVersion: 1,
    members,
    ...over,
  }) as unknown as IProjectDocument

class World {
  readonly chats = new Map<string, ScopedLoadedChat>()
  readonly projects = new Map<string, IProjectDocument>()
  readonly teams = new FakeTeams()
  flagOn = true
  server!: http.Server
  origin = ''

  chat(seed: ChatSeed): string {
    const session = {
      _id: new Types.ObjectId(seed.id),
      orgId: new Types.ObjectId(ORG),
      userId: new Types.ObjectId(seed.owner ?? A),
      isDeleted: false,
      sharedWith: (seed.sharedWith ?? []).map((r) => ({ ...r, principalType: r.userId ? 'user' : 'team' })),
      projectId: seed.projectId ? new Types.ObjectId(seed.projectId) : undefined,
      projectVisibility: seed.projectVisibility,
      settings: {},
      aclVersion: 1,
    } as unknown as ScopedSession
    const project = seed.projectId ? this.projects.get(seed.projectId) : undefined
    this.chats.set(seed.id, { session, project: project ? projectFactsOf(project) : null })
    return seed.id
  }

  project(id: string, owner: string, members: Member[], over: Record<string, unknown> = {}): string {
    this.projects.set(id, projectDoc(id, owner, members, over))
    return id
  }

  async start(): Promise<void> {
    const loader: IChatAccessLoader & IScopedChatLoader = {
      load: async (_org, id): Promise<LoadedChat | null> => this.fresh(id),
      loadScoped: async (_org, target) => this.fresh(target.id),
    }
    const projects: IProjectAccessPort = {
      roleOf: async (subject, id) => {
        const project = this.projects.get(id)
        if (!project) return null
        const role = projectRoleFor(project, subject)
        return role === 'none' ? null : { role, project }
      },
      assertAtLeast: async () => {
        throw new Error('not used')
      },
      accessibleProjectIds: async () => [],
    }
    const flags = { isEnabled: async (): Promise<boolean> => this.flagOn }
    const authz = new AuthorizationService({ chats: loader, projects, flags })
    const guards = new ConversationGuards({
      authz,
      chats: loader,
      projects,
      flags,
      users: { displayNames: async () => new Map(), findByIds: async () => [] },
      teams: this.teams,
      logger: { warn: () => undefined },
    })
    const tokens = new JwtServiceTokenIssuer(new AuthTokenService('jwt-secret', SCOPED_SECRET))
    const controller = new AuthzController(
      new ExplainService(authz, loader, async (userId) => userId === ADMIN),
      new AccessPreviewService(loader, projects),
      new SubjectTeamResolver(this.teams, tokens),
    )
    const container = new Container()
    container.bind('AuthMiddleware').toConstantValue({ authenticate: headerAuth })
    container.bind(COLLAB_TYPES.FeatureFlags).toConstantValue(flags)
    container.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(guards)
    container.bind(COLLAB_TYPES.AuthzController).toConstantValue(controller)
    const app = express()
    app.use(express.json())
    app.use('/api/v1/authz', createAuthzRouter(container))
    app.use(ErrorMiddleware.handleError())
    this.server = http.createServer(app)
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve))
    this.origin = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`
  }

  private fresh(id: string): ScopedLoadedChat | null {
    const found = this.chats.get(id)
    if (!found) return null
    const projectId = found.session.projectId?.toString()
    const project = projectId ? this.projects.get(projectId) : undefined
    return { session: found.session, project: project ? projectFactsOf(project) : null }
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => this.server.close(() => resolve()))
  }

  async call(method: 'GET' | 'POST', path: string, as: { userId: string; scopes?: string[] } | null, body?: unknown) {
    const res = await fetch(`${this.origin}/api/v1/authz${path}`, {
      method,
      headers: {
        ...(as && { 'x-test-user': JSON.stringify({ userId: as.userId, orgId: ORG, scopes: as.scopes }) }),
        ...(body !== undefined && { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? (JSON.parse(text) as any) : undefined }
  }

  explain(as: string, chat: string, subject?: string, extra = '') {
    const subjectQ = subject ? `&subject=user:${subject}` : ''
    return this.call('GET', `/explain?resource=chat:${chat}${subjectQ}${extra}`, { userId: as })
  }

  preview(as: string, chat: string, change: unknown) {
    return this.call('POST', '/explain/preview', { userId: as }, { resource: `chat:${chat}`, change })
  }
}

const ids = (rows: Array<{ userId?: string; teamId?: string }>): string[] => rows.map((r) => r.userId ?? r.teamId ?? '').sort()

describe('user-facing authz routes (PH07-20)', () => {
  let w: World
  beforeEach(async () => {
    w = new World()
    await w.start()
  })
  afterEach(() => w.stop())

  describe('GET /explain', () => {
    it('explains the caller to themself: owner, direct row and a team row they belong to', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'write' }, { teamId: T_SALES, accessLevel: 'read' }] })
      w.teams.byUser.set(B, [T_SALES])
      const res = await w.explain(B, chat)
      expect(res.status).to.equal(200)
      expect(res.body.role).to.equal('editor')
      expect(res.body.via).to.have.deep.members([
        { type: 'direct', ref: B, role: 'editor' },
        { type: 'team', ref: T_SALES, role: 'viewer' },
      ])
      expect(await w.explain(A, chat)).to.deep.include({ status: 200 })
    })

    it('lets the chat owner explain another subject, resolving that subject teams on the server', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ teamId: T_SALES, accessLevel: 'write' }] })
      w.teams.byUser.set(B, [T_SALES])
      w.teams.byUser.set(A, [T_SALES])
      const res = await w.explain(A, chat, B)
      expect(res.status).to.equal(200)
      expect(res.body).to.deep.equal({ role: 'editor', via: [{ type: 'team', ref: T_SALES, role: 'editor' }] })
      const lookup = w.teams.calls.find((c) => c.userId === B)
      expect(lookup, 'B teams were looked up').to.not.equal(undefined)
      const claims = jwt.verify(lookup!.authorization!.replace('Bearer ', ''), SCOPED_SECRET) as jwt.JwtPayload
      expect(claims).to.include({ userId: B, orgId: ORG })
      expect(claims.scopes).to.deep.equal(['team:ids:read'])
      expect(claims.exp! - claims.iat!).to.be.at.most(60)
    })

    it('lets an org admin explain a subject on a chat they do not own', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'read' }] })
      const res = await w.explain(ADMIN, chat, B)
      expect(res.status).to.equal(200)
      expect(res.body.role).to.equal('viewer')
    })

    it('refuses a stranger, with the same 403 whether or not the chat exists and no team lookup for the subject', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'write' }] })
      const real = await w.explain(D, chat, B)
      const missing = await w.explain(D, oid(), B)
      expect(real.status).to.equal(403)
      expect(missing.status).to.equal(403)
      expect(missing.body).to.deep.equal(real.body)
      expect(w.teams.calls.filter((c) => c.userId === B)).to.have.length(0)
    })

    it('does not let a collaborator, even an editor, explain someone else', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'write' }, { userId: C, accessLevel: 'read' }] })
      expect((await w.explain(B, chat, C)).status).to.equal(403)
    })

    it('answers a stranger explaining themself with role none, the same for an unknown chat', async () => {
      const chat = w.chat({ id: oid() })
      const known = await w.explain(D, chat)
      const unknown = await w.explain(D, oid())
      expect(known).to.deep.equal({ status: 200, body: { role: 'none', via: [] } })
      expect(unknown).to.deep.equal(known)
    })

    it('redacts the ref of a team the caller does not belong to', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ teamId: T_SALES, accessLevel: 'write' }, { teamId: T_OPS, accessLevel: 'read' }] })
      w.teams.byUser.set(B, [T_SALES, T_OPS])
      w.teams.byUser.set(A, [T_OPS])
      const res = await w.explain(A, chat, B)
      expect(res.status).to.equal(200)
      expect(res.body.via).to.have.deep.members([
        { type: 'team', ref: null, role: 'editor' },
        { type: 'team', ref: T_OPS, role: 'viewer' },
      ])
    })

    it('refuses a client-supplied teamIds and never uses it', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ teamId: T_SALES, accessLevel: 'write' }] })
      const res = await w.explain(A, chat, B, `&teamIds=${T_SALES}`)
      expect(res.status).to.equal(400)
      const viaBody = await w.call('POST', '/explain/preview', { userId: A }, { resource: `chat:${chat}`, change: { type: 'unlink' }, teamIds: [T_SALES] })
      expect(viaBody.status).to.equal(400)
      // The server's own answer for B (no teams) shows no team path.
      const honest = await w.explain(A, chat, B)
      expect(honest.body).to.deep.equal({ role: 'none', via: [] })
    })

    it('fails closed with 503 when the subject teams cannot be resolved and a team row could matter', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ teamId: T_SALES, accessLevel: 'write' }] })
      w.teams.down = true
      const res = await w.explain(A, chat, B)
      expect(res.status).to.equal(503)
      expect(res.body.error.code).to.equal('TEAM_RESOLUTION_UNAVAILABLE')
      expect((await w.explain(B, chat)).status).to.equal(503)
    })

    it('still answers when teams are down but the chat has no team rows', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'read' }] })
      w.teams.down = true
      const res = await w.explain(A, chat, B)
      expect(res.status).to.equal(200)
      expect(res.body.role).to.equal('viewer')
    })

    it('rejects a malformed resource or subject', async () => {
      expect((await w.call('GET', '/explain?resource=chat:nope', { userId: A })).status).to.equal(400)
      expect((await w.call('GET', `/explain?resource=project:${oid()}`, { userId: A })).status).to.equal(400)
      expect((await w.call('GET', `/explain?resource=chat:${oid()}&subject=team:x`, { userId: A })).status).to.equal(400)
    })

    it('requires the conversation:read scope', async () => {
      const res = await w.call('GET', `/explain?resource=chat:${oid()}`, { userId: A, scopes: ['project:read'] })
      expect(res.status).to.equal(403)
    })
  })

  describe('POST /explain/preview', () => {
    const P1 = oid()
    const P2 = oid()

    it('unlink: project-only viewers lose access, direct collaborators keep it, and nothing is written', async () => {
      w.project(P1, A, [user(PM), user(B, 'editor')])
      const chat = w.chat({ id: oid(), projectId: P1, projectVisibility: 'project', sharedWith: [{ userId: B, accessLevel: 'write' }, { userId: C, accessLevel: 'read' }] })
      const before = JSON.stringify({ chats: [...w.chats.values()], projects: [...w.projects.values()] })
      const res = await w.preview(A, chat, { type: 'unlink' })
      expect(res.status).to.equal(200)
      expect(res.body.loses).to.deep.equal([{ userId: PM, role: 'viewer' }])
      expect(res.body.gains).to.deep.equal([])
      expect(res.body.becomesReadOnly).to.deep.equal([])
      expect(res.body.truncated).to.equal(false)
      expect(JSON.stringify({ chats: [...w.chats.values()], projects: [...w.projects.values()] })).to.equal(before)
    })

    it('link: project members gain, collaborators outside the project can only read', async () => {
      w.project(P2, A, [user(PM2)], { chatSharing: 'members' })
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'write' }, { userId: C, accessLevel: 'read' }] })
      const res = await w.preview(A, chat, { type: 'link', projectId: P2 })
      expect(res.status).to.equal(200)
      expect(res.body.gains).to.deep.equal([{ userId: PM2, role: 'viewer' }])
      expect(res.body.becomesReadOnly).to.deep.equal([{ userId: B, role: 'viewer' }])
      expect(res.body.loses).to.deep.equal([])
    })

    it('link to a project that keeps chats private grants project members nothing', async () => {
      w.project(P2, A, [user(PM2)])
      const chat = w.chat({ id: oid() })
      const res = await w.preview(A, chat, { type: 'link', projectId: P2 })
      expect(res.body.gains).to.deep.equal([])
    })

    it('visibility: flipping a project chat to private removes inherited viewers; to project adds them', async () => {
      w.project(P1, A, [user(PM), team(T_SALES)], { projectChatAccess: 'editor' })
      const chat = w.chat({ id: oid(), projectId: P1, projectVisibility: 'private' })
      const up = await w.preview(A, chat, { type: 'visibility', visibility: 'project' })
      expect(ids(up.body.gains)).to.deep.equal([PM, T_SALES].sort())
      w.chat({ id: chat, projectId: P1, projectVisibility: 'project' })
      const down = await w.preview(A, chat, { type: 'visibility', visibility: 'private' })
      expect(ids(down.body.loses)).to.deep.equal([PM, T_SALES].sort())
    })

    it('visibility on a chat that is not in a project is a 400', async () => {
      const chat = w.chat({ id: oid() })
      expect((await w.preview(A, chat, { type: 'visibility', visibility: 'project' })).status).to.equal(400)
    })

    it('owner only: an editor gets 403 CONVERSATION_OWNER_ONLY', async () => {
      const chat = w.chat({ id: oid(), sharedWith: [{ userId: B, accessLevel: 'write' }] })
      const res = await w.preview(B, chat, { type: 'unlink' })
      expect(res.status).to.equal(403)
      expect(res.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
    })

    it('a stranger and an unknown chat are the same 404', async () => {
      const chat = w.chat({ id: oid() })
      const stranger = await w.preview(D, chat, { type: 'unlink' })
      const missing = await w.preview(D, oid(), { type: 'unlink' })
      expect(stranger.status).to.equal(404)
      expect(missing).to.deep.equal(stranger)
    })

    it('a link to a project the caller cannot open is a 404 that names no member', async () => {
      w.project(P2, D, [user(PM2, 'editor')])
      const chat = w.chat({ id: oid() })
      const res = await w.preview(A, chat, { type: 'link', projectId: P2 })
      const unknown = await w.preview(A, chat, { type: 'link', projectId: oid() })
      expect(res.status).to.equal(404)
      expect(JSON.stringify(res.body)).to.not.include(PM2)
      expect(unknown.body.error.code).to.equal(res.body.error.code)
    })

    it('unlinking from a project the owner can no longer open does not list its members', async () => {
      w.project(P1, D, [user(PM)])
      const chat = w.chat({ id: oid(), projectId: P1, projectVisibility: 'project' })
      const res = await w.preview(A, chat, { type: 'unlink' })
      expect(res.status).to.equal(200)
      expect(res.body.loses).to.deep.equal([])
    })

    it('caps the principals it evaluates and says so', async () => {
      const many = Array.from({ length: PREVIEW_PRINCIPAL_CAP + 20 }, () => user(oid()))
      w.project(P1, A, many)
      const chat = w.chat({ id: oid(), projectId: P1, projectVisibility: 'project' })
      const res = await w.preview(A, chat, { type: 'unlink' })
      expect(res.body.loses).to.have.length(PREVIEW_PRINCIPAL_CAP)
      expect(res.body.truncated).to.equal(true)
    })

    it('rejects an unknown change type', async () => {
      const chat = w.chat({ id: oid() })
      expect((await w.preview(A, chat, { type: 'merge' })).status).to.equal(400)
    })
  })

  describe('flag off', () => {
    it('answers 404 on both routes, before authentication', async () => {
      w.flagOn = false
      const chat = w.chat({ id: oid() })
      expect((await w.explain(A, chat)).status).to.equal(404)
      expect((await w.preview(A, chat, { type: 'unlink' })).status).to.equal(404)
      expect((await w.call('GET', `/explain?resource=chat:${chat}`, null)).status).to.equal(404)
      expect(w.teams.calls).to.have.length(0)
    })
  })
})

