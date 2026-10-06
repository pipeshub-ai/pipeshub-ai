import { NextFunction, Response } from 'express'
import { ConversationGuards, ListKind, ListScopeOptions } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'

export interface ListScopeEnv {
  collab?: boolean
  teamIds?: string[]
  projectIds?: string[]
  /** Overrides `projectIds` to model membership that depends on the caller's teams. */
  projectIdsFor?: (subject: { teamIds: readonly string[] | 'unresolved' }) => string[]
}

/** Mirrors the options `GET /conversations` mounts. */
export const chatListScope: ListScopeOptions = {
  includeOwned: (req) => req.query.source !== 'shared',
  includeShared: (req) => req.query.source === 'shared',
  archived: 'exclude',
}

export const chatArchivesScope: ListScopeOptions = { includeProjects: false, archived: 'only' }
export const agentListScope: ListScopeOptions = { shareRows: 'never', sharedWithMeList: true, archived: 'exclude' }
export const agentArchivesScope: ListScopeOptions = { includeShared: false, archived: 'only' }

type Handler = (req: never, res: never, next: never) => Promise<unknown>

export function listGuards(env: ListScopeEnv = {}): ConversationGuards {
  return new ConversationGuards({
    authz: {} as never,
    chats: {} as never,
    projects: { accessibleProjectIds: async (subject: { teamIds: readonly string[] | 'unresolved' }) => env.projectIdsFor?.(subject) ?? env.projectIds ?? [] } as never,
    flags: { isEnabled: async () => env.collab ?? false },
    users: {} as never,
    teams: { callerTeamIds: async () => ({ status: 'ok', teamIds: env.teamIds ?? [] }) } as never,
  })
}

/** Runs the real `listScope` guard for the request, then the handler, as the route does. */
export function withListScope<H extends Handler>(
  handler: H,
  kind: ListKind,
  options: ListScopeOptions = {},
  env: ListScopeEnv | (() => ListScopeEnv) = {},
): H {
  return (async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const guards = listGuards(typeof env === 'function' ? env() : env)
    let guardError: unknown
    await guards.listScope(kind, options)(req, res, (err?: unknown) => {
      guardError = err
    })
    if (guardError !== undefined) {
      next(guardError)
      return undefined
    }
    return (handler as unknown as (...a: unknown[]) => Promise<unknown>)(req, res, next)
  }) as unknown as H
}

/** Runs the real `caller()` guard for the request, then the handler, as the route does. */
export function withCallerScope<H extends Handler>(handler: H, env: ListScopeEnv = {}): H {
  return (async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    let guardError: unknown
    await listGuards(env).caller()(req, res, (err?: unknown) => {
      guardError = err
    })
    if (guardError !== undefined) {
      next(guardError)
      return undefined
    }
    return (handler as unknown as (...a: unknown[]) => Promise<unknown>)(req, res, next)
  }) as unknown as H
}
