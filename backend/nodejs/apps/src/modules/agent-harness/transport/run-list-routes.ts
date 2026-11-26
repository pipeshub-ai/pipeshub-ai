/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import {
  BadRequestError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import type { RunListService } from '../application/run-lists';
import { AGENT_HARNESS_RUN_LISTS } from '../binders/tokens';
import {
  decodeRunListCursor,
  MAX_RUN_LIST_LIMIT,
  RUN_LIST_KINDS,
  RUN_LIST_STATES,
  RunListState,
} from '../domain/run-lists';
import { transportError } from './errors';
import { identityString } from './request-identity';

export const AGENT_HARNESS_RUNS_PREFIX = '/api/v1/agent-harness/runs';

const time = z.coerce.number().int().min(0).max(Number.MAX_SAFE_INTEGER);

export const listPersonRunsSchema = z.object({
  query: z
    .object({
      /** One list word or several, comma-separated (`working,needs_you`). */
      state: z.string().min(1).max(100).optional(),
      agentKey: z.string().min(1).max(128).optional(),
      kind: z.enum(RUN_LIST_KINDS).optional(),
      from: time.optional(),
      to: time.optional(),
      cursor: z.string().min(1).max(400).optional(),
      limit: z.coerce.number().int().min(1).max(MAX_RUN_LIST_LIMIT).optional(),
    })
    .strict(),
});

const states = (value: string | undefined): RunListState[] | undefined => {
  if (value === undefined) return undefined;
  const words = value.split(',').map((word) => word.trim());
  const unknown = words.filter(
    (word) => !(RUN_LIST_STATES as readonly string[]).includes(word),
  );
  if (unknown.length > 0) {
    throw new BadRequestError(
      `Unknown run state: ${unknown.join(', ')} (one of ${RUN_LIST_STATES.join(', ')})`,
    );
  }
  return [...new Set(words)] as RunListState[];
};

/**
 * ADR-UI-01 D1 (UI-B1 G1, G7): `GET /api/v1/agent-harness/runs`, the caller's own runs and jobs across their
 * conversations, newest first; with `agentKey`, an agent's Runs tab. The org and the person are the token's, never a
 * parameter, so nobody lists another person's runs here.
 */
export function createAgentHarnessRunsRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');
  const service = (): RunListService => {
    if (!container.isBound(AGENT_HARNESS_RUN_LISTS)) {
      throw new ServiceUnavailableError(
        'Agent runs are not available on this deployment',
      );
    }
    return container.get<RunListService>(AGENT_HARNESS_RUN_LISTS);
  };

  router.get(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.CONVERSATION_READ),
    ValidationMiddleware.validate(listPersonRunsSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const user = req.user as Record<string, unknown> | undefined;
        const orgId = identityString(user?.['orgId']);
        const actorId = identityString(user?.['userId']);
        if (orgId === null || actorId === null) {
          throw new UnauthorizedError(
            'Authenticated user identity is incomplete',
          );
        }
        const { query } = listPersonRunsSchema.parse({ query: req.query });
        const after =
          query.cursor !== undefined
            ? decodeRunListCursor(query.cursor)
            : undefined;
        if (after === null) {
          throw new BadRequestError(
            'The page cursor is not one this server handed out',
          );
        }
        const filter = states(query.state);
        const page = await service().personRuns(
          { orgId, actorId },
          {
            ...(filter !== undefined ? { states: filter } : {}),
            ...(query.agentKey !== undefined
              ? { agentKey: query.agentKey }
              : {}),
            ...(query.kind !== undefined ? { kind: query.kind } : {}),
            ...(query.from !== undefined ? { createdFrom: query.from } : {}),
            ...(query.to !== undefined ? { createdTo: query.to } : {}),
            ...(after !== undefined ? { after } : {}),
            ...(query.limit !== undefined ? { limit: query.limit } : {}),
          },
        );
        res.status(200).json(page);
      } catch (error) {
        next(transportError(error));
      }
    },
  );
  return router;
}
