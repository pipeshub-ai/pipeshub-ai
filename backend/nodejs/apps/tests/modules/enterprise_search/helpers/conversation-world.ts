import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { RequestHandler, Router } from 'express'
import { Types } from 'mongoose'
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../../src/modules/enterprise_search/routes/es.routes'
import { CONVERSATION_TYPES } from '../../../../src/modules/enterprise_search/services/conversation/conversation.types'
import { CONVERSATION_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/conversation/domain/errors'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { FakeAIBackend, InMemoryChatStore, oid } from '../controller/chat-test-harness'
import { realGuards, stopAfterGuard } from './guarded-chat'
import { turnDeps } from './turn-deps'
import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/conversation/turn/turn-deps'
import { invokeRoute, RouteOutcome } from './route-invoker'
import { AGENT_KEY, ConversationRoute, urlOf } from './conversation-routes'

/** A seeded org with one chat and one agent chat shared in every way, and its actors; used by the router-level access suites. */
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
  /** Role a policy that honours every share row would give this actor. */
  role: Role
  /** Access path types that exist for this actor (team rows are ignored by the default policy). */
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
  // Project editor: read through the project, as every project member is.
  PE: actor('write', ['project']),
  D: actor('none', []),
  E: actor('none', [], [], OTHER_ORG),
}
export const ACTOR_NAMES = Object.keys(ACTORS) as ActorName[]

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
  container: Container
}

export function buildRouters(options: {
  stop?: boolean
  keyValueStore?: object
  /** Replaces the default handler dependencies. */
  deps?: ConversationTurnDeps
  /** Replaces the pass-through authentication, for suites that drive the routers over HTTP. */
  authenticate?: RequestHandler
} = {}): Env {
  const real = realGuards()
  const container = new Container()
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: options.authenticate ?? ((_req: unknown, _res: unknown, next: () => void) => next()),
    scopedTokenValidator: () => (_req: unknown, _res: unknown, next: () => void) => next(),
  })
  container.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai.test', jwtSecret: 'j', scopedJwtSecret: 's', iamBackend: 'http://iam.test' })
  if (options.keyValueStore) container.bind('KeyValueStoreService').toConstantValue(options.keyValueStore)
  container.bind(CONVERSATION_TYPES.ConversationGuards).toConstantValue(options.stop === false ? real : stopAfterGuard(real))
  container.bind(CONVERSATION_TYPES.ConversationTurnDeps).toConstantValue(options.deps ?? turnDeps())
  return { chat: createConversationalRouter(container), agent: createAgentConversationalRouter(container), container }
}

export type Expected = 'allow' | { status: 403 | 404; code: string }
export const NOT_FOUND: Expected = { status: 404, code: CONVERSATION_ERROR_CODES.NOT_FOUND }

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

