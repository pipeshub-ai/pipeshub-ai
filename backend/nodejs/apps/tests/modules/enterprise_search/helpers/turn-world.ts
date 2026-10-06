import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { FixedClock } from '../../../../src/libs/types/clock'
import { ConversationGuards } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { LeaseHandle } from '../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { MongoRunLeaseManager } from '../../../../src/modules/enterprise_search/services/collaboration/leases/run-lease.manager'
import { ConversationTurnDeps } from '../../../../src/modules/enterprise_search/services/collaboration/turn/turn-deps'
import { FakeSSEResponse, settle } from '../controller/chat-test-harness'
import { AGENT_KEY } from './conversation-routes'
import { ACTORS, ActorName, Env, Seed, TEAM_READ, TEAM_WRITE, buildRouters, seed, teamsOf } from './conversation-world'
import { CONVERSATION_ROUTES, urlOf } from './conversation-routes'
import { realGuards } from './guarded-chat'
import { invokeRoute, RouteOutcome } from './route-invoker'
import { turnDeps } from './turn-deps'
import { COLLAB_FLAG_KEYS } from '../../../../src/modules/configuration_manager/constants/constants'
import { INewChatSharing } from '../../../../src/modules/enterprise_search/services/collaboration/conversation-collaboration.service'
import { IAgentDirectory, IAgentProfiles } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/agent.directory'
import { MentionTurnGate } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention-turn-gate'
import { MentionValidator } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'

const NAMES: Record<string, string> = { A: 'Alice', B: 'Bob', Bt: 'Beth', C: 'Carol', Ct: 'Cindy', D: 'Dan', E: 'Eve' }

export type Kind = 'chat' | 'agent'
export type Mode = 'stream' | 'plain'

const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never

export interface TurnMentionOptions {
  /** The mentions flag; default on. */
  enabled?: boolean
  agents?: IAgentDirectory
  /** The agent builder flag: with it, any agent the sender may run is mentionable (guest turns, M2). Default off. */
  guestAgents?: boolean
  /** Names and handles of guest agents for the history and the read side. */
  profiles?: IAgentProfiles
}

export interface TurnWorldOptions {
  collab?: boolean
  /** Wires the mention gate into the guards and the notes routes into the routers (PH-10.4). Absent: not wired, as before. */
  mentions?: TurnMentionOptions
  /** Shares a chat as it is created (first-send `share`). */
  sharing?: INewChatSharing
  /** Heartbeat period of the lease manager; the default is the production 30 s. */
  heartbeatMs?: number
  deps?: Partial<ConversationTurnDeps>
  /** The author of the seeded questions (legacy rows have none and read as the owner's). */
  questionAuthor?: Types.ObjectId
  /** The user directory is down (D11). */
  ownerDirectoryDown?: boolean
  assertAtLeast?: sinon.SinonStub
}

export interface Turn {
  req: any
  res: FakeSSEResponse
  /** What the chain passed to `next(err)`: a guard denial or a handler error before the stream opened. */
  error?: Error & { statusCode?: number; code?: string; publicDetails?: Record<string, unknown> }
  /** The handler ran (the guards let the request through). */
  reached: boolean
  lease?: LeaseHandle
}

/**
 * A seeded world with the real guards, a real lease manager over the in-memory store, and the follow-up handlers:
 * `start` runs `authorize('send')`, `runLease()` and the handler exactly as the route does.
 */
export function turnWorld(over: Record<string, unknown> = {}, options: TurnWorldOptions = {}) {
  const s: Seed = seed(over, options.questionAuthor)
  const clock = new FixedClock(Date.parse('2026-10-02T10:00:00Z'))
  const base = new MongoRunLeaseManager({
    clock,
    instanceId: 'pod',
    heartbeatMs: options.heartbeatMs,
    logger: { warn: sinon.stub() },
    users: { displayNames: async () => new Map([[String(ACTORS.A.userId), 'Alice']]), findByIds: async () => [] },
  })
  const handles: LeaseHandle[] = []
  const hooks: { beforeAcquire?: () => void } = {}
  const leases = {
    forNewSession: (userId: string, orgId: string) => {
      const fresh = base.forNewSession(userId, orgId)
      return {
        activeRun: fresh.activeRun,
        bind: (sessionId: string) => {
          const handle = fresh.bind(sessionId)
          sinon.spy(handle, 'release')
          handles.push(handle)
          return handle
        },
      }
    },
    acquire: async (...args: Parameters<MongoRunLeaseManager['acquire']>) => {
      hooks.beforeAcquire?.()
      const handle = await base.acquire(...args)
      sinon.spy(handle, 'release')
      handles.push(handle)
      return handle
    },
  }
  const readiness = { check: sinon.stub().resolves({ status: 'ready' }), invalidate: sinon.stub() }
  const mentionFlag = { on: options.mentions?.enabled ?? true }
  const agents: IAgentDirectory = options.mentions?.agents ?? { canExecute: sinon.stub().resolves(true), isServiceAccount: sinon.stub().resolves(false) }
  const worldUsers = Object.fromEntries(
    (['A', 'B', 'Bt', 'C', 'Ct', 'D', 'E'] as ActorName[]).map((n) => [String(ACTORS[n].userId), { displayName: NAMES[n], orgId: ACTORS[n].orgId }]),
  )
  const teamMembers = { [TEAM_WRITE]: [String(ACTORS.Bt.userId)], [TEAM_READ]: [String(ACTORS.Ct.userId)] }
  const collaboration = { users: worldUsers, teamMembers, agents, mentions: mentionFlag.on, teamsOf }
  const mentionGate = options.mentions
    ? new MentionTurnGate({
        flags: { isEnabled: async (key: string) => key === COLLAB_FLAG_KEYS.chatMentions && mentionFlag.on },
        readiness: readiness as never,
        validator: new MentionValidator({
          flags: { isEnabled: async (key: string) => key === COLLAB_FLAG_KEYS.chatAgentBuilder && options.mentions?.guestAgents === true },
          users: {
            displayNames: async () => new Map(),
            findByIds: async (org, ids) =>
              ids.flatMap((id) =>
                worldUsers[id] && String(worldUsers[id].orgId) === org ? [{ userId: id, displayName: worldUsers[id].displayName, kind: 'human' as const, isDisabled: false }] : [],
              ),
          },
          teams: {
            callerTeamIds: async () => ({ status: 'ok', teamIds: [] }),
            teamsVersion: async () => 0,
            exists: async () => true,
            memberUserIds: async (teamId) => (teamMembers[teamId] ? { status: 'ok', userIds: teamMembers[teamId] } : { status: 'unresolved' }),
          },
          agents,
        }),
      })
    : undefined
  const guards: ConversationGuards = realGuards({
    mentions: mentionGate,
    collab: options.collab ?? true,
    teamIds: teamsOf,
    leases: leases as never,
    readiness: readiness as never,
    ownerDirectoryDown: options.ownerDirectoryDown,
    assertAtLeast: options.assertAtLeast,
  })
  const deps = turnDeps({ leases: leases as never, mentions: mentionGate, agents: options.mentions?.profiles, sharing: options.sharing, ...options.deps })

  const handlerFor = (kind: Kind, mode: Mode) => {
    if (mode === 'stream') {
      return kind === 'chat' ? controller.addMessageStream(appConfig, deps) : controller.addMessageStreamToAgentConversation(appConfig, deps)
    }
    return kind === 'chat' ? controller.addMessage(appConfig, deps) : controller.addMessageToAgentConversation(appConfig, deps)
  }

  const start = (who: ActorName, kind: Kind = 'chat', mode: Mode = 'stream', body: Record<string, unknown> = {}): Promise<Turn> => {
    const a = ACTORS[who]
    const req: any = {
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com` },
      params: { conversationId: s.ids[kind], messageId: s.messageIds[kind], ...(kind === 'agent' && { agentKey: AGENT_KEY }) },
      body: { query: 'More detail', ...body },
      query: {},
      headers: { authorization: `Bearer token-${who}` },
      context: { requestId: 'req-turn' },
      on: () => req,
    }
    const res = new FakeSSEResponse()
    const turn: Turn = { req, res, reached: false }
    return new Promise<Turn>((resolve) => {
      const fail = (error: unknown): void => {
        turn.error = error as Turn['error']
        resolve(turn)
      }
      const handler = handlerFor(kind, mode) as unknown as (r: unknown, s: unknown, n: (e?: unknown) => void) => Promise<void>
      guards.authorize('send', kind)(req, res as never, (e?: unknown) => {
        if (e !== undefined) return fail(e)
        void guards.runLease(kind)(req, res as never, (e2?: unknown) => {
          if (e2 !== undefined) return fail(e2)
          turn.reached = true
          turn.lease = handles[handles.length - 1]
          handler(req, res, (e3?: unknown) => {
            if (e3 !== undefined) fail(e3)
          }).then(
            () => resolve(turn),
            (e4: unknown) => fail(e4),
          )
        })
      })
    })
  }

  const createHandler = (kind: Kind, mode: Mode) => {
    if (mode === 'stream') {
      return kind === 'chat' ? controller.streamChat(appConfig, deps) : controller.streamAgentConversation(appConfig, deps)
    }
    return kind === 'chat' ? controller.createConversation(appConfig, deps) : controller.createAgentConversation(appConfig, deps)
  }

  /** A first send: `caller()` and the create handler, exactly as the route mounts them. The conversation does not exist yet. */
  const startCreate = (who: ActorName, kind: Kind = 'chat', mode: Mode = 'stream', body: Record<string, unknown> = {}): Promise<Turn> => {
    const a = ACTORS[who]
    const req: any = {
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com` },
      params: kind === 'agent' ? { agentKey: AGENT_KEY } : {},
      body: { query: 'First question', ...body },
      query: {},
      headers: { authorization: `Bearer token-${who}` },
      context: { requestId: 'req-create' },
      on: () => req,
    }
    const res = new FakeSSEResponse()
    // Recorded among the store's writes so a test can see which came first, the SSE head or the session.
    const writeHead = res.writeHead.bind(res)
    res.writeHead = (...args) => {
      s.store.writes.push('writeHead')
      return writeHead(...args)
    }
    const turn: Turn = { req, res, reached: false }
    return new Promise<Turn>((resolve) => {
      const fail = (error: unknown): void => {
        turn.error = error as Turn['error']
        resolve(turn)
      }
      const handler = createHandler(kind, mode) as unknown as (r: unknown, s: unknown, n: (e?: unknown) => void) => Promise<void>
      guards.caller()(req, res as never, (e?: unknown) => {
        if (e !== undefined) return fail(e)
        turn.reached = true
        handler(req, res, (e2?: unknown) => {
          if (e2 !== undefined) fail(e2)
        }).then(
          () => resolve(turn),
          (e3: unknown) => fail(e3),
        )
      })
    })
  }

  const seededIds = new Set(s.store.sessions.map((x) => String(x._id)))
  /** Conversations a first send created, oldest first. */
  const created = (): Array<Record<string, any>> => s.store.sessions.filter((x) => !seededIds.has(String(x._id))).map((x) => x.toObject() as Record<string, any>)
  const rowsOf = (sessionId: unknown): Array<Record<string, any>> => s.store.messagesOf(sessionId) as Array<Record<string, any>>

  /** Regenerates the answer `messageId` (default: the seeded one): `authorize('regenerate')`, `runLease()` and the handler, as the route mounts them. */
  const startRegenerate = (who: ActorName, kind: Kind = 'chat', body: Record<string, unknown> = {}, messageId: string = s.messageIds[kind]): Promise<Turn> => {
    const a = ACTORS[who]
    const req: any = {
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com` },
      params: { conversationId: s.ids[kind], messageId, ...(kind === 'agent' && { agentKey: AGENT_KEY }) },
      body: { chatMode: 'quick', ...body },
      query: {},
      headers: { authorization: `Bearer token-${who}` },
      context: { requestId: 'req-regen' },
      on: () => req,
    }
    const res = new FakeSSEResponse()
    const turn: Turn = { req, res, reached: false }
    return new Promise<Turn>((resolve) => {
      const fail = (error: unknown): void => {
        turn.error = error as Turn['error']
        resolve(turn)
      }
      const handler = (kind === 'chat' ? controller.regenerateAnswers(appConfig, deps) : controller.regenerateAgentAnswers(appConfig, deps)) as unknown as (
        r: unknown,
        s: unknown,
        n: (e?: unknown) => void,
      ) => Promise<void>
      guards.authorize('regenerate', kind)(req, res as never, (e?: unknown) => {
        if (e !== undefined) return fail(e)
        void guards.runLease(kind, { op: 'regenerate' })(req, res as never, (e2?: unknown) => {
          if (e2 !== undefined) return fail(e2)
          turn.reached = true
          turn.lease = handles[handles.length - 1]
          handler(req, res, (e3?: unknown) => {
            if (e3 !== undefined) fail(e3)
          }).then(
            () => resolve(turn),
            (e4: unknown) => fail(e4),
          )
        })
      })
    })
  }

  /** One request through the real router (validators, guards, handler) for a PH-04 route id such as `C3` or `C12`. */
  let routers: Env | undefined
  const ensureRouters = (): Env => {
    routers ??= buildRouters({
      collab: options.collab ?? true,
      ownerDirectoryDown: options.ownerDirectoryDown,
      stop: false,
      leases: leases as never,
      readiness: readiness as never,
      keyValueStore: {},
      deps,
      mentions: mentionGate,
      collaboration,
    })
    return routers
  }
  const invoke = (who: ActorName, routeId: string, body: Record<string, unknown> = {}, ids: { messageId?: string } = {}): Promise<RouteOutcome> => {
    const route = CONVERSATION_ROUTES.find((r) => r.id === routeId)!
    const a = ACTORS[who]
    return invokeRoute(ensureRouters()[route.kind], {
      method: route.method,
      url: urlOf(route, { conversationId: s.ids[route.kind], messageId: ids.messageId ?? s.messageIds[route.kind] }),
      body: { ...route.body, ...body },
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com` },
    })
  }

  /** Any route of the real router by its suffix after the conversation id, for the PH-10 routes the PH-04 table does not list. */
  const request = (who: ActorName, kind: Kind, method: string, suffix: string, body?: Record<string, unknown>): Promise<RouteOutcome> => {
    const a = ACTORS[who]
    return invokeRoute(ensureRouters()[kind], {
      method,
      url: kind === 'chat' ? `/${s.ids.chat}${suffix}` : `/${AGENT_KEY}/conversations/${s.ids.agent}${suffix}`,
      body,
      user: { userId: String(a.userId), orgId: String(a.orgId), email: `${who}@example.com` },
    })
  }

  /**
   * Parks an `ask_user_question` card after the last row, as a turn that asked leaves it. `by` is the person it was put to;
   * omitted, the row is a legacy one with no `requestedBy`.
   */
  const parkCard = (kind: Kind = 'chat', by?: ActorName, extra: Record<string, unknown> = {}): Record<string, any> => {
    const doc = s.store.addMessage(s.store.session(s.ids[kind])!, {
      messageType: 'tool_call',
      content: '',
      tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: 'Which?' }] } }],
      ...(by && { requestedBy: ACTORS[by].userId }),
      ...extra,
    })
    return doc.toObject() as Record<string, any>
  }

  const session = (kind: Kind = 'chat') => s.store.session(s.ids[kind])!.toObject() as Record<string, any>
  const rows = (kind: Kind = 'chat') => s.store.messagesOf(s.ids[kind]) as Array<Record<string, any>>
  /** Answers the streaming turn: one text delta, then the final answer, then the end of the stream. */
  const answerStream = async (text = 'The answer.', extra: Record<string, unknown> = {}): Promise<void> => {
    s.ai.send('TEXT_MESSAGE_CONTENT', { runId: 'run-root', messageId: 'm1', delta: text })
    s.ai.send('RUN_FINISHED', { runId: 'run-root', result: { answer: text, citations: [], confidence: 'High', ...extra } })
    s.ai.finish()
    await settle()
  }
  /** Another run takes the lease, as after an expiry. */
  const takeOver = (kind: Kind = 'chat', who: ActorName = 'B'): void => {
    s.store.session(s.ids[kind])!.set('activeRun', {
      runId: 'run-of-someone-else',
      userId: ACTORS[who].userId,
      startedAt: new Date(clock.now()),
      leaseExpiresAt: new Date(clock.now() + 120_000),
    })
  }
  return { s, clock, guards, agents, mentionFlag, routers: () => routers, env: ensureRouters, collaboration, handles, hooks, readiness, leases, deps, start, startCreate, startRegenerate, invoke, request, parkCard, created, rowsOf, session, rows, answerStream, takeOver }
}

export type TurnWorld = ReturnType<typeof turnWorld>
