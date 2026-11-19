import { NextFunction, Response, Router } from 'express';
import { Container } from 'inversify';
import { promisify } from 'util';
import { gzip } from 'zlib';
import { z } from 'zod';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import { relayPost } from './code-graph.routes';

const gzipAsync = promisify(gzip);
const TIMEOUT_MS = 30_000;
// Below this a gzip header costs more than it saves.
const GZIP_MIN_BYTES = 1024;

/** The scope a source needs, checked per request because the source is in the body. */
const SOURCE_SCOPES = { code: OAuthScopeNames.CODE_READ } as const;
const source = z.enum(['code']);
const nodeId = z.string().min(1).max(200);

const expandSchema = z.object({
  body: z.object({
    source,
    nodeIds: z.array(nodeId).min(1).max(20),
    direction: z.enum(['inbound', 'outbound', 'any']).optional(),
    edgeTypes: z.array(z.string().max(40)).max(20).optional(),
    includeTests: z.boolean().optional(),
    limit: z.number().int().min(1).max(100).optional(),
    cursor: z.string().max(256).optional(),
  }),
});

const resolveSchema = z.object({
  body: z
    .object({
      source,
      repo: z.string().max(300).optional(),
      recordId: nodeId.optional(),
      filePath: z.string().min(1).max(2000).optional(),
      qualifiedName: z.string().min(1).max(2000).optional(),
      includeTests: z.boolean().optional(),
    })
    .refine((b) => b.recordId || (b.repo && b.filePath), {
      message: 'Give recordId, or repo and filePath.',
    }),
});

const revalidateSchema = z.object({
  body: z.object({
    source,
    nodeIds: z.array(nodeId).min(1).max(5000),
    includeTests: z.boolean().optional(),
  }),
});

/** Sets `Cache-Control` before anything can fail, so a refusal carries it too. */
const noStore = (
  _req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): void => {
  res.setHeader('Cache-Control', 'private, no-store');
  next();
};

const requireSourceScope = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): void => {
  const { source } = req.body as { source: keyof typeof SOURCE_SCOPES };
  requireScopes(SOURCE_SCOPES[source])(req, res, next);
};

/** A big graph is mostly repeated ids and names: Express has no compression, so the relay does it. */
async function send(
  req: AuthenticatedUserRequest,
  res: Response,
  data: unknown,
): Promise<void> {
  const json = JSON.stringify(data ?? {});
  res.status(200).type('application/json').setHeader('Vary', 'Accept-Encoding');
  if (
    Buffer.byteLength(json) >= GZIP_MIN_BYTES &&
    req.acceptsEncodings('gzip') === 'gzip'
  ) {
    res.setHeader('Content-Encoding', 'gzip');
    res.send(await gzipAsync(json));
    return;
  }
  res.send(json);
}

/**
 * The graph explorer's reads: find a node, expand its neighbours and revalidate
 * nodes a client kept. The query service decides what the caller may see, with the
 * caller's own token; nothing here filters, stores or caches.
 */
export function createGraphViewRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  const relay = (
    path: string,
    schema: z.ZodObject,
    operation: string,
  ): Router =>
    router.post(
      path,
      noStore,
      authMiddleware.authenticate.bind(authMiddleware),
      ValidationMiddleware.validate(schema),
      requireSourceScope,
      async (
        req: AuthenticatedUserRequest,
        res: Response,
        next: NextFunction,
      ) => {
        try {
          await send(
            req,
            res,
            await relayPost(
              appConfig,
              req,
              `/api/v1/graph${path}`,
              operation,
              TIMEOUT_MS,
            ),
          );
        } catch (error) {
          next(error);
        }
      },
    );

  relay('/expand', expandSchema, 'Expanding the graph');
  relay('/resolve', resolveSchema, 'Finding the graph node');
  relay('/revalidate', revalidateSchema, 'Checking graph nodes');

  return router;
}
