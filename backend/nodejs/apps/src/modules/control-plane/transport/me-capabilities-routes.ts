import { NextFunction, Request, Response, Router } from 'express';
import type { Container } from 'inversify';
import {
  HttpError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import type { AuthenticatedUserRequest } from '../../../libs/middlewares/types';

export const CONTROL_PLANE_ME_CAPABILITIES_PATH =
  '/api/v1/control-plane/me/capabilities';

/** The container key of the `MeCapabilitiesSource` (bound by the host that routes chat turns). */
export const CONTROL_PLANE_ME_CAPABILITIES = 'ControlPlaneMeCapabilities';

/** Where a chat turn runs, and why when it is today's loop. */
export interface TurnRoute {
  path: 'durable' | 'legacy';
  reason: string | null;
}

/** By chat mode, for a turn to an agent and a turn to the built-in assistant. */
export interface TurnRoutes {
  agent: Record<string, TurnRoute>;
  assistant: Record<string, TurnRoute>;
}

/**
 * What the caller's turns would get, decided by whoever decides them: the
 * control plane states the contract and the host supplies the answer.
 * Rejects when a route cannot be decided; one is never guessed.
 */
export interface MeCapabilitiesSource {
  /** The org head the answer is as of: it moves with every write that can change a route. */
  headRevision(orgId: string): Promise<number>;
  /** A new conversation without a project, on the org's default model, in every chat mode. */
  routing(orgId: string, userId: string): Promise<TurnRoutes>;
}

const etagOf = (headRevision: number): string => `"${String(headRevision)}"`;

/** `If-None-Match` names the tag, among those it lists (a weak validator compares equal). */
const matches = (header: string | undefined, etag: string): boolean =>
  (header ?? '')
    .split(',')
    .map((tag) => tag.trim().replace(/^W\//, ''))
    .some((tag) => tag === etag || tag === '*');

/**
 * `GET /api/v1/control-plane/me/capabilities` (CPD-10 §6.3): how the server
 * would route the caller's next turn per chat mode, so a client shows what a
 * mode does without deciding it. The org and the user are the token's.
 */
export function createMeCapabilitiesRouter(container: Container): Router {
  const router = Router();
  router.get(
    '/',
    (req: Request, res: Response, next: NextFunction) =>
      container
        .get<AuthMiddleware>('AuthMiddleware')
        .authenticate(req as AuthenticatedUserRequest, res, next),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const user = (req as AuthenticatedUserRequest).user;
        const orgId: unknown = user?.orgId;
        const userId: unknown = user?.userId;
        if (typeof orgId !== 'string' || typeof userId !== 'string') {
          throw new UnauthorizedError('No user in the request');
        }
        if (!container.isBound(CONTROL_PLANE_ME_CAPABILITIES)) {
          throw new ServiceUnavailableError(
            'Routing is not configured on this deployment',
          );
        }
        const source = container.get<MeCapabilitiesSource>(
          CONTROL_PLANE_ME_CAPABILITIES,
        );
        const headRevision = await source.headRevision(orgId);
        const etag = etagOf(headRevision);
        res.setHeader('ETag', etag);
        res.setHeader('Cache-Control', 'private, no-cache');
        if (matches(req.header('if-none-match'), etag)) {
          res.status(304).end();
          return;
        }
        res.status(200).json({
          routing: await source.routing(orgId, userId),
          headRevision,
        });
      } catch (error) {
        next(
          error instanceof HttpError
            ? error
            : new ServiceUnavailableError('Routing cannot be decided now'),
        );
      }
    },
  );
  return router;
}
