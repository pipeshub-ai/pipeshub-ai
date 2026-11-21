/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { ServiceUnavailableError } from '../../../libs/errors/http.errors';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { MemoryService, MemoryViewer } from '../application/memory-service';
import { AGENT_HARNESS_MEMORIES } from '../composition';
import { HarnessMemoryRefusedError } from '../domain/errors';
import { MAX_MEMORY_CHARS, MEMORY_STATUSES } from '../domain/memory';
import { passConflict } from './policy-revisions';
import { transportError } from './errors';
import { currentOrgContext } from '../../../libs/context/orgContext';

export const MEMORIES_PREFIX = '/api/v1/agent-harness/memories';

const memoryId = z.string().min(1).max(128);

export const listMemoriesSchema = z.object({
  query: z.object({ status: z.enum(MEMORY_STATUSES).optional() }).strict(),
});

export const editMemorySchema = z.object({
  params: z.object({ memoryId }),
  body: z.object({ content: z.string().min(1).max(MAX_MEMORY_CHARS) }).strict(),
});

export const memoryParamsSchema = z.object({
  params: z.object({ memoryId }),
});

export const memoryChoiceSchema = z.object({
  body: z.object({ remember: z.boolean() }).strict(),
});

export const reviewMemorySchema = z.object({
  params: z.object({ memoryId }),
  body: z.object({ decision: z.enum(['accepted', 'rejected']) }).strict(),
});

type Handler = (req: AuthenticatedUserRequest, res: Response) => Promise<void>;

const handle =
  (handler: Handler) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      await handler(req, res);
    } catch (error) {
      passConflict(res, next, error, transportError);
    }
  };

/**
 * A connected app must not plant, edit or approve what a user's runs are
 * told: memory changes are the user's own session's or token's.
 */
const firstPartyOnly = (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void => {
  if (req.user?.isOAuth === true) {
    next(
      transportError(
        new HarnessMemoryRefusedError(
          'MEMORY_WRITE_NOT_GRANTED',
          'Memories are managed by the user, not by connected apps',
        ),
      ),
    );
    return;
  }
  next();
};

function service(container: Container): MemoryService {
  if (!container.isBound(AGENT_HARNESS_MEMORIES)) {
    throw new ServiceUnavailableError(
      'Memory is not configured on this deployment',
    );
  }
  return container.get<MemoryService>(AGENT_HARNESS_MEMORIES);
}

const viewer = (req: AuthenticatedUserRequest): MemoryViewer => {
  const requestId = req.headers['x-request-id'];
  return {
    orgId: String(currentOrgContext().orgId),
    userId: String(req.user?.userId),
    authorization:
      typeof req.headers.authorization === 'string'
        ? req.headers.authorization
        : undefined,
    ...(typeof requestId === 'string' ? { requestId } : {}),
  };
};

/**
 * A user's own memories (P7.3): the owner is always the caller, never a path
 * segment. Lists show what the caller may read now; anything else is not
 * found.
 */
export function createMemoryRouter(container: Container): Router {
  const router = Router();
  const auth = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(listMemoriesSchema),
    handle(async (req, res) => {
      const { status } = req.query as z.infer<
        typeof listMemoriesSchema
      >['query'];
      const memories = await service(container).listOwn(
        viewer(req),
        status === undefined ? undefined : [status],
      );
      res.status(200).json({ memories });
    }),
  );

  // Before the `/:memoryId` routes: the person's own switch, not a memory.
  router.get(
    '/preference',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    handle(async (req, res) => {
      res.status(200).json(await service(container).ownChoice(viewer(req)));
    }),
  );

  router.put(
    '/preference',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(memoryChoiceSchema),
    handle(async (req, res) => {
      const { remember } = req.body as z.infer<
        typeof memoryChoiceSchema
      >['body'];
      res
        .status(200)
        .json(await service(container).setOwnChoice(viewer(req), remember));
    }),
  );

  router.patch(
    '/:memoryId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(editMemorySchema),
    handle(async (req, res) => {
      const { content } = req.body as z.infer<typeof editMemorySchema>['body'];
      res
        .status(200)
        .json(
          await service(container).editOwn(
            viewer(req),
            String(req.params.memoryId),
            content,
          ),
        );
    }),
  );

  router.delete(
    '/:memoryId',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    ValidationMiddleware.validate(memoryParamsSchema),
    handle(async (req, res) => {
      await service(container).deleteOwn(
        viewer(req),
        String(req.params.memoryId),
      );
      res.status(204).end();
    }),
  );

  router.post(
    '/:memoryId/review',
    auth.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    firstPartyOnly,
    ValidationMiddleware.validate(reviewMemorySchema),
    handle(async (req, res) => {
      const { decision } = req.body as z.infer<
        typeof reviewMemorySchema
      >['body'];
      const memory = await service(container).reviewOwn(
        viewer(req),
        String(req.params.memoryId),
        decision,
      );
      res.status(200).json({ decision, memory });
    }),
  );

  return router;
}
