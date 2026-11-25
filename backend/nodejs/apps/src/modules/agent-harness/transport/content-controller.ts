import express, { NextFunction, RequestHandler, Response } from 'express';
import { ZodError } from 'zod';
import { BadRequestError, HttpError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import {
  ContentTooLargeError,
  RunContentService,
} from '../application/run-content-service';
import { HarnessUnauthorizedError, harnessWireCode } from '../domain/errors';
import { CONTENT_MAX_BYTES, ContentRefusal } from '../domain/run-content';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import { readRunContentSchema, writeRunContentSchema } from './schemas';

const CONTENT_TYPE = 'application/octet-stream';

const tooLarge = (): HttpError =>
  new HttpError(
    harnessWireCode('CONTENT_TOO_LARGE'),
    new ContentTooLargeError().message,
    HTTP_STATUS.PAYLOAD_TOO_LARGE,
  );

/**
 * The raw body, bounded; mounted after the attempt token so an
 * unauthenticated caller cannot make Node buffer anything.
 */
export function rawContentBody(
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  const parse = express.raw({ type: CONTENT_TYPE, limit: CONTENT_MAX_BYTES });
  const refuse = (req: HarnessAttemptRequest, reason: ContentRefusal): void => {
    logger.warn(
      'content.refused',
      harnessLogMeta('content.refused', {
        orgId: req.harnessAttempt?.orgId,
        runId: req.harnessAttempt?.runId,
        attemptId: req.harnessAttempt?.attemptId,
        fence: req.harnessAttempt?.fence,
        reason,
      }),
    );
  };
  return (req, res, next) => {
    parse(req, res, (error?: unknown) => {
      const attempt = req as HarnessAttemptRequest;
      if (
        (error as { type?: unknown } | undefined)?.type === 'entity.too.large'
      ) {
        refuse(attempt, 'too_large');
        next(tooLarge());
        return;
      }
      if (error !== undefined) {
        next(error);
        return;
      }
      if (!Buffer.isBuffer(req.body)) {
        refuse(attempt, 'unsupported_media');
        next(
          new HttpError(
            harnessWireCode('CONTENT_MEDIA_TYPE'),
            `Content must be sent as ${CONTENT_TYPE}`,
            HTTP_STATUS.UNSUPPORTED_MEDIA_TYPE,
          ),
        );
        return;
      }
      next();
    });
  };
}

/** DP-1: workers write and read sealed content; Node holds the keys. */
export class AgentHarnessContentController {
  constructor(
    private readonly content: RunContentService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  private refused(req: HarnessAttemptRequest, error: unknown): unknown {
    if (error instanceof HarnessUnauthorizedError) {
      logAuthorityRefusal(this.logger, req, error);
    }
    if (error instanceof ContentTooLargeError) return tooLarge();
    if (error instanceof ZodError) {
      return new BadRequestError('Invalid content request');
    }
    return transportError(error);
  }

  put = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const query = writeRunContentSchema.shape.query.parse(req.query);
      await this.authority.authorizeAttempt(claims, query);
      const stored = await this.content.put(claims, req.body as Buffer);
      res.status(HTTP_STATUS.OK).json(stored);
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  get = async (
    req: HarnessAttemptRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const claims = attemptClaims(req);
      const query = readRunContentSchema.shape.query.parse(req.query);
      await this.authority.authorizeAttempt(claims, query);
      const bytes = await this.content.get(claims, query.ref);
      res
        .status(HTTP_STATUS.OK)
        .type(CONTENT_TYPE)
        .setHeader('Cache-Control', 'no-store')
        .end(bytes);
    } catch (error) {
      next(this.refused(req, error));
    }
  };
}
