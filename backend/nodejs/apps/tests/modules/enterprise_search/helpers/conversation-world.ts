import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { RequestHandler, Router } from 'express'
import { Types } from 'mongoose'
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../../src/modules/enterprise_search/routes/es.routes'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { FakeAIBackend, InMemoryChatStore, oid } from '../controller/chat-test-harness'
import { realGuards, stopAfterGuard } from './guarded-chat'
import { turnDeps } from './turn-deps'
import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/collaboration/turn/turn-deps'
import { IRunLeaseManager } from '../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { IMentionTurnGate } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention-turn-gate'
import { IAgentReadinessPort } from '../../../../src/modules/enterprise_search/services/collaboration/readiness/agent-readiness.port'
import { invokeRoute, RouteOutcome } from './route-invoker'
import { bindCollaborationWorld, CollaborationWorld, CollaborationWorldOptions } from './collaboration-world'
import { AGENT_KEY, ConversationRoute, urlOf } from './conversation-routes'

/** A seeded org with one chat and one agent chat shared in every way, and the actors of 60 §10; used by the router-level access suites. */
export const ORG = oid()
export const OTHER_ORG = oid()
export const PROJECT = oid()
export const TEAM_WRITE = 'team-write'
export const TEAM_READ = 'team-read'

export type ActorName = 'A' | 'B' | 'Bt' | 'C' | 'Ct' | 'P' | 'PE' | 'D' | 'E'
export type Role = 'owner' | 'write' | 'read' | 'none'

export interface Actor {
  userId: Types.ObjectId
  orgId: Types.ObjectId
  /** Role the seed gives this actor with the flag on. */
  role: Role
  /** Access path types that exist for this actor (team rows are ignored by the flag-off policy). */
  paths: Array<'owner' | 'direct' | 'team' | 'project'>
  teams: string[]
}

export const actor = (role: Role, paths: Actor['paths'], teams: string[] = [], orgId = ORG): Actor => ({ userId: oid(), orgId, role, paths, teams })

export const ACTORS: Record<ActorName, Actor> = {
  A: actor('owner', ['owner']),
  B: actor('write', ['direct']),
  Bt: actor('write', ['team'], [TEAM_WRITE]),
  C: actor('read', ['direct']),
  Ct: actor('read', ['team'], [TEAM_READ]),
  P: actor('read', ['project']),
  // Project editor under a project whose chats grant `editor`: write with the flag on, read through the ceiling otherwise.
  PE: actor('write', ['project']),
  D: actor('none', []),
  E: actor('none', [], [], OTHER_ORG),
}
export const ACTOR_NAMES = Object.keys(ACTORS) as ActorName[]
export const teamsOf = (userId: string): string[] => ACTOR_NAMES.map((n) => ACTORS[n]).find((a) => String(a.userId) === userId)?.teams ?? []

export interface Seed {
  store: InMemoryChatStore
  ai: FakeAIBackend
  ids: Record<'chat' | 'agent', string>
  messageIds: Record<'chat' | 'agent', string>
}

/** A fresh world; stubs of an earlier seed in the same test are released first. */
export function seed(over: Record<string, unknown> = {}, questionAuthor?: Types.ObjectId): Seed {
  sinon.restore()
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  const project = {
    _id: PROJECT,
    orgId: ORG,
    userId: oid(),
    visibility: 'private',
    aclVersion: 1,
    projectChatAccess: 'editor',
    members: [
      { principalType: 'user', principalId: ACTORS.P.userId, role: 'viewer' },
      { principalType: 'user', principalId: ACTORS.PE.userId, role: 'editor' },
    ],
  }
  sinon.stub(Project, 'findOne').callsFake((() => Object.assign(Promise.resolve(project), { lean: () => Promise.resolve(project) })) as never)
  const base = {
    orgId: ORG,
    userId: ACTORS.A.userId,
    initiator: ACTORS.A.userId,
    isShared: true,
    sharedWith: [
      { userId: ACTORS.C.userId, accessLevel: 'read' },
      { userId: ACTORS.B.userId, accessLevel: 'write' },
      { teamId: TEAM_WRITE, accessLevel: 'write' },
      { teamId: TEAM_READ, accessLevel: 'read' },
    ],
    projectId: PROJECT,
    projectVisibility: 'project',
    ...over,
  }
  const make = (kind: 'chat' | 'agent') => {
    const session = store.addSession({
      ...base,
      title: kind,
      sessionType: kind,
      ...(kind === 'agent' && { agentKey: AGENT_KEY, conversationSource: 'agent_chat' }),
    })
    store.addMessage(session, { messageType: 'user_query', content: 'question', ...(questionAuthor && { authorUserId: questionAuthor }) })
    const bot = store.addMessage(session, { messageType: 'bot_response', content: 'answer' })
    return { id: String(session._id), message: String(bot._id) }
  }
  const chat = make('chat')
  const agent = make('agent')
  store.writes.length = 0
  return { store, ai, ids: { chat: chat.id, agent: agent.id }, messageIds: { chat: chat.message, agent: agent.message } }
}

export interface Env {
  chat: Router
  agent: Router
  teamCalls: { count: number }
  collaboration: CollaborationWorld
  container: Container
}

export function buildRouters(options: {
  collab: boolean
  ownerActive?: boolean
  ownerDirectoryDown?: boolean
  stop?: boolean
  keyValueStore?: object
  /** For `runLease()`; routers that never reach it can omit them. */
  leases?: IRunLeaseManager
  readiness?: IAgentReadinessPort
  /** Replaces the default handler collaborators; first-send routes need a lease manager here. */
  deps?: ConversationTurnDeps
  /** Overrides of the collaboration fakes (users, teams, flags). */
  collaboration?: Partial<Omit<CollaborationWorldOptions, 'collab'>>
  /** The mention gate of the real guards (PH-10.4); sends are then validated and classified before the lease. */
  mentions?: IMentionTurnGate
  /** Replaces the pass-through authentication, for suites that drive the routers over HTTP. */
  authenticate?: RequestHandler
}): Env {
  const teamCalls = { count: 0 }
  const real = realGuards({ collab: options.collab, ownerActive: options.ownerActive, ownerDirectoryDown: options.ownerDirectoryDown, teamIds: options.collaboration?.teamsOf ?? teamsOf, teamCalls, leases: options.leases, readiness: options.readiness, mentions: options.mentions })
  const container = new Container()
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: options.authenticate ?? ((_req: unknown, _res: unknown, next: () => void) => next()),
    scopedTokenValidator: () => (_req: unknown, _res: unknown, next: () => void) => next(),
  })
  container.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai.test', jwtSecret: 'j', scopedJwtSecret: 's', iamBackend: 'http://iam.test' })
  if (options.keyValueStore) container.bind('KeyValueStoreService').toConstantValue(options.keyValueStore)
  container.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(options.stop === false ? real : stopAfterGuard(real))
  container.bind(COLLAB_TYPES.ConversationTurnDeps).toConstantValue(options.deps ?? turnDeps())
  const collaboration = bindCollaborationWorld(container, {
    collab: options.collab,
    orgId: ORG,
    teamsOf,
    ...options.collaboration,
  })
  return { chat: createConversationalRouter(container), agent: createAgentConversationalRouter(container), teamCalls, collaboration, container }
}

export type Expected = 'allow' | { status: 403 | 404; code: string }
export const NOT_FOUND: Expected = { status: 404, code: COLLAB_ERROR_CODES.NOT_FOUND }

export async function call(env: Env, route: ConversationRoute, s: Seed, who: ActorName, kind: 'chat' | 'agent' = route.kind): Promise<RouteOutcome> {
  const a = ACTORS[who]
  return invokeRoute(env[route.kind], {
    method: route.method,
    url: urlOf(route, { conversationId: s.ids[kind], messageId: s.messageIds[kind] }),
    body: route.body,
    user: { userId: a.userId, orgId: a.orgId, email: `${who}@example.com` },
  })
}

export function assertDecision(out: RouteOutcome, expected: Expected, label: string): void {
  if (expected === 'allow') {
    expect(out.error, `${label}: guard let the request through`).to.equal(undefined)
    expect(out.status, label).to.equal(299)
    return
  }
  expect(out.error?.statusCode, `${label} status`).to.equal(expected.status)
  expect(out.error?.code, `${label} code`).to.equal(expected.code)
  expect((out.body as { error: { code: string } }).error.code, `${label} wire code`).to.equal(expected.code)
  expect(out.res.headersSent, `${label}: no SSE header`).to.equal(false)
}

export const nothingHappened = (s: Seed): void => {
  expect(s.store.writes, 'writes').to.deep.equal([])
  expect(s.ai.calls, 'AI calls').to.deep.equal([])
  expect(s.ai.streamCalls, 'AI stream calls').to.deep.equal([])
}

