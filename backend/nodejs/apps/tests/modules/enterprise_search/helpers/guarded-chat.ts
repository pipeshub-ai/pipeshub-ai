import sinon from 'sinon'
import { NextFunction, Request, RequestHandler, Response } from 'express'
import { DefaultConversationGuards } from '../../../../src/modules/enterprise_search/services/conversation/http/default-conversation-guards'
import { GUARD_MARK, GuardMarkValue, IConversationGuards } from '../../../../src/modules/enterprise_search/services/conversation/http/conversation-guards'
import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/conversation/domain/types'

/** The real default guards; only the project port is stubbed. */
export function realGuards(): DefaultConversationGuards {
  return new DefaultConversationGuards({
    projects: {
      accessibleProjectIds: sinon.stub().resolves([]),
      roleOf: sinon.stub().resolves(null),
      assertAtLeast: sinon.stub().resolves({ _id: 'project' }),
    },
  } as never)
}

type Handler = (req: never, res: never, next: never) => unknown

/** Runs the route's guard, then the handler; a denial goes to `next(err)` and the handler never runs, as in the router. */
export function behindGuard(
  guards: IConversationGuards,
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
export function markingGuards(): IConversationGuards {
  const pass: RequestHandler = (_req, _res, next) => next()
  const marked = (value: GuardMarkValue): RequestHandler =>
    Object.defineProperty(((req, res, next) => pass(req, res, next)) as RequestHandler, GUARD_MARK, { value })
  return {
    authorize: (op: ConversationOperation, kind: 'chat' | 'agent') => marked({ op, kind }),
    listScope: (kind: 'chat' | 'agent' | 'any') => marked({ list: true, kind }),
    caller: () => marked({ caller: true }),
    runLease: (kind: 'chat' | 'agent') => marked({ lease: true, kind }),
  }
}

/**
 * The real guards, except that a request the guard lets through is answered 299 `{guardPassed:true}` instead of reaching the
 * handler. Lets a router-level suite test the access decision alone, without driving every handler to completion.
 */
export function stopAfterGuard<G extends IConversationGuards>(guards: G): G {
  const stop = (guard: RequestHandler): RequestHandler => {
    const wrapped: RequestHandler = (req, res, next) =>
      void guard(req, res, (err?: unknown) => (err === undefined ? res.status(299).json({ guardPassed: true }) : next(err as never)))
    return wrapped
  }
  return Object.assign(Object.create(guards) as G, {
    authorize: (op: ConversationOperation, kind: 'chat' | 'agent') => stop(guards.authorize(op, kind)),
  })
}
