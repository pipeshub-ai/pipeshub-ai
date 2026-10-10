import sinon from 'sinon'
import { NextFunction, Request, RequestHandler, Response } from 'express'
import { AuthorizationService } from '../../../../src/modules/authz/authz.service'
import { ChatAccessLoader } from '../../../../src/modules/authz/loaders/chat.loader'
import { ConversationGuards, GUARD_MARK, GuardMarkValue } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { IRunLeaseManager } from '../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'
import { IMentionTurnGate } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention-turn-gate'
import { IAgentReadinessPort } from '../../../../src/modules/enterprise_search/services/collaboration/readiness/agent-readiness.port'
import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'

export interface GuardedChatOptions {
  collab?: boolean
  /** Teams of every caller, or per caller (userId). */
  teamIds?: string[] | ((userId: string) => string[])
  ownerActive?: boolean
  /** The user directory throws, as in an outage (D11). */
  ownerDirectoryDown?: boolean
  /** Counts team directory reads. */
  teamCalls?: { count: number }
  /** Wired into the guards for `runLease()`; absent means not configured. */
  leases?: IRunLeaseManager
  readiness?: IAgentReadinessPort
  /** Team resolution always answers `unresolved`. */
  teamsUnresolved?: boolean
  /** Replaces the project port's `assertAtLeast`; the default stub resolves a bare project. */
  assertAtLeast?: sinon.SinonStub
  /** The mention gate `runLease()` runs before the lease (PH-10.4). */
  mentions?: IMentionTurnGate
}

/** The real guards over the real PDP; only the flag, directories and project port are stubbed. */
export function realGuards(options: GuardedChatOptions = {}): ConversationGuards {
  const flags = { isEnabled: sinon.stub().resolves(options.collab ?? false) }
  const chats = new ChatAccessLoader()
  const projects = {
    accessibleProjectIds: sinon.stub().resolves([]),
    roleOf: sinon.stub().resolves(null),
    assertAtLeast: options.assertAtLeast ?? sinon.stub().resolves({ _id: 'project' }),
  }
  return new ConversationGuards({
    authz: new AuthorizationService({ chats, projects, flags }),
    chats,
    projects,
    flags,
    users: {
      displayNames: sinon.stub().resolves(new Map()),
      findByIds: sinon.stub().callsFake((_org: string, ids: readonly string[]) =>
        options.ownerDirectoryDown
          ? Promise.reject(new Error('user directory down'))
          : Promise.resolve(ids.map((userId) => ({ userId, displayName: 'u', kind: 'human', isDisabled: options.ownerActive === false }))),
      ),
    },
    teams: {
      callerTeamIds: sinon.stub().callsFake((identity: { userId: string }) => {
        if (options.teamCalls) options.teamCalls.count += 1
        const teamIds = typeof options.teamIds === 'function' ? options.teamIds(identity.userId) : (options.teamIds ?? [])
        return Promise.resolve(options.teamsUnresolved ? { status: 'unresolved' } : { status: 'ok', teamIds })
      }),
      teamsVersion: sinon.stub().resolves(0),
      exists: sinon.stub().resolves(true),
    },
    leases: options.leases,
    readiness: options.readiness,
    mentions: options.mentions,
    logger: { warn: sinon.stub() },
  })
}

type Handler = (req: never, res: never, next: never) => unknown

/** Runs the route's guard, then the handler; a denial goes to `next(err)` and the handler never runs, as in the router. */
export function behindGuard(
  guards: ConversationGuards,
  op: ConversationOperation,
  handler: Handler,
  kind: 'chat' | 'agent' = 'chat',
): (req: never, res: never, next: never) => Promise<void> {
  const guard: RequestHandler = guards.authorize(op, kind)
  return (req, res, next) =>
    new Promise<void>((resolve, reject) => {
      const forward: NextFunction = (err?: unknown) => {
        if (err !== undefined) {
          ;(next as unknown as NextFunction)(err)
          resolve()
          return
        }
        Promise.resolve(handler(req, res, next)).then(() => resolve(), reject)
      }
      void guard(req as unknown as Request, res as unknown as Response, forward)
    })
}

/** Guards that mark each layer and pass through, so route-table tests can read `{op, kind}` off `router.stack`. */
export function markingGuards(): ConversationGuards {
  const pass: RequestHandler = (_req, _res, next) => next()
  const marked = (value: GuardMarkValue): RequestHandler =>
    Object.defineProperty(((req, res, next) => pass(req, res, next)) as RequestHandler, GUARD_MARK, { value })
  return {
    authorize: (op: ConversationOperation, kind: 'chat' | 'agent') => marked({ op, kind }),
    listScope: (kind: 'chat' | 'agent' | 'any') => marked({ list: true, kind }),
    caller: () => marked({ caller: true }),
    runLease: (kind: 'chat' | 'agent') => marked({ lease: true, kind }),
  } as unknown as ConversationGuards
}

/**
 * The real guards, except that a request the guard lets through is answered 299 `{guardPassed:true}` instead of reaching the
 * handler. Lets a router-level suite test the access decision alone, without driving every handler to completion.
 */
export function stopAfterGuard(guards: ConversationGuards): ConversationGuards {
  const stop = (guard: RequestHandler): RequestHandler => {
    const wrapped: RequestHandler = (req, res, next) =>
      void guard(req, res, (err?: unknown) => (err === undefined ? res.status(299).json({ guardPassed: true }) : next(err as never)))
    return wrapped
  }
  return Object.assign(Object.create(guards) as ConversationGuards, {
    authorize: (op: ConversationOperation, kind: 'chat' | 'agent') => stop(guards.authorize(op, kind)),
  })
}
