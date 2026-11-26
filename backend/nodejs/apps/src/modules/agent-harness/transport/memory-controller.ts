import { NextFunction, Response } from 'express';
import { z, ZodError } from 'zod';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import { MemoryService } from '../application/memory-service';
import { AttemptClaims } from '../domain/authority';
import {
  HarnessMemoryRefusedError,
  HarnessUnauthorizedError,
} from '../domain/errors';
import { transportError as publicError } from './errors';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';

const boundedId = z.string().min(1).max(256);
const runParams = z.object({ runId: boundedId });
const attempt = {
  attemptId: boundedId,
  fence: z.coerce.number().int().nonnegative(),
};

export const recallMemoriesSchema = z.object({
  params: runParams,
  query: z.object(attempt).strict(),
});

export const proposeMemorySchema = z.object({
  params: runParams,
  body: z
    .object({
      ...attempt,
      content: z.string().min(1).max(2000),
      kind: z.string().min(1).max(32),
      scope: z.string().min(1).max(32),
      replaces: z.string().min(1).max(128).nullable().optional(),
    })
    .strict(),
});

type Handler = (
  req: HarnessAttemptRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

/**
 * Worker routes of governed memory (P7.3), attempt token only: the run's
 * pinned memories, and the proposals its model makes. The run is the token's;
 * nothing in a request names another run, actor or org.
 */
export class AgentHarnessMemoryController {
  constructor(
    private readonly memories: MemoryService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  recall = this.handle(async (claims, req) => {
    const { query } = recallMemoriesSchema.parse(req);
    await this.authority.authorizeAttempt(claims, query);
    return {
      memories: await this.memories.recallForRun(claims.orgId, claims.runId),
    };
  });

  propose = this.handle(async (claims, req) => {
    const { body } = proposeMemorySchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.memories.propose(claims.orgId, claims.runId, {
      content: body.content,
      kind: body.kind,
      scope: body.scope,
      ...(body.replaces !== undefined ? { replaces: body.replaces } : {}),
    });
  });

  private handle(
    work: (
      claims: AttemptClaims,
      req: HarnessAttemptRequest,
    ) => Promise<unknown>,
  ): Handler {
    return async (req, res, next) => {
      try {
        res.status(HTTP_STATUS.OK).json(await work(attemptClaims(req), req));
      } catch (error) {
        next(this.refused(req, error));
      }
    };
  }

  private refused(req: HarnessAttemptRequest, error: unknown): unknown {
    if (error instanceof HarnessUnauthorizedError) {
      logAuthorityRefusal(this.logger, req, error);
    }
    if (error instanceof ZodError) {
      this.logger.warn(
        'memory.refused',
        harnessLogMeta('memory.refused', {
          orgId: req.harnessAttempt?.orgId,
          runId: req.harnessAttempt?.runId,
          reason: 'invalid_request',
        }),
      );
      return new BadRequestError('Invalid memory request');
    }
    if (error instanceof HarnessMemoryRefusedError) return publicError(error);
    return transportError(error);
  }
}
