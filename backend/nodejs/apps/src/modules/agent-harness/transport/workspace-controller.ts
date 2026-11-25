import { NextFunction, Response } from 'express';
import { ZodError } from 'zod';
import {
  BadRequestError,
  ExpectedRefusalError,
} from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HarnessAuthority } from '../application/harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import { WorkspaceService } from '../application/workspace-service';
import { AttemptClaims } from '../domain/authority';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import { HarnessUnauthorizedError, harnessWireCode } from '../domain/errors';
import { MAX_WORKSPACE_FILES, WorkspaceError } from '../domain/workspace';
import { HarnessAttemptRequest, logAuthorityRefusal } from './internal-auth';
import { attemptClaims, transportError } from './internal-controller';
import {
  workspaceCommitSchema,
  workspaceForkSchema,
  workspaceHeadSchema,
  workspaceKeySchema,
  workspaceMergeSchema,
  workspaceRevisionSchema,
  workspaceRollbackSchema,
  workspaceWriterReleaseSchema,
  workspaceWriterSchema,
} from './schemas';

/** A workspace refusal as the HTTP error a client reads (`AGENT_HARNESS_<code>`). */
export const workspaceRefusal = (error: WorkspaceError): ExpectedRefusalError =>
  new ExpectedRefusalError(
    harnessWireCode(error.code),
    error.message,
    ERROR_CODES[error.code].status,
  );

/**
 * A commit listing more files than a workspace holds is the store's size refusal, not a malformed request (WP-82):
 * the worker reads `WORKSPACE_TOO_LARGE` and keeps its deliverables, where a bare 400 told it nothing.
 */
const overFileCap = (error: ZodError): boolean =>
  error.issues.length > 0 &&
  error.issues.every(
    (issue) =>
      issue.code === 'too_big' &&
      issue.origin === 'array' &&
      issue.path.length === 2 &&
      issue.path[0] === 'body' &&
      (issue.path[1] === 'upserts' || issue.path[1] === 'deletes'),
  );

/** Where a request failed its schema, for the operator: field paths and codes only, never values. */
const zodIssues = (error: ZodError): string =>
  error.issues
    .slice(0, 5)
    .map((issue) => `${issue.path.slice(0, 4).join('.')}:${issue.code}`)
    .join(',');

type Handler = (
  req: HarnessAttemptRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

/** Worker routes of the session workspace store (P3.2); attempt token only. */
export class AgentHarnessWorkspaceController {
  constructor(
    private readonly workspaces: WorkspaceService,
    private readonly authority: HarnessAuthority,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  head = this.handle(async (claims, req) => {
    const { params, query } = workspaceHeadSchema.parse(req);
    await this.authority.authorizeAttempt(claims, query);
    return this.workspaces.head(claims, params.workspaceId);
  });

  revision = this.handle(async (claims, req) => {
    const { params, query } = workspaceRevisionSchema.parse(req);
    await this.authority.authorizeAttempt(claims, query);
    return this.workspaces.revision(
      claims,
      params.workspaceId,
      params.revision,
    );
  });

  commit = this.handle(async (claims, req) => {
    const { params, body } = workspaceCommitSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.commit(claims, params.workspaceId, {
      baseRevision: body.baseRevision,
      reason: body.reason,
      changes: { upserts: body.upserts, deletes: body.deletes },
    });
  });

  fork = this.handle(async (claims, req) => {
    const { params, body } = workspaceForkSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.fork(claims, params.workspaceId, body);
  });

  merge = this.handle(async (claims, req) => {
    const { params, body } = workspaceMergeSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.merge(claims, params.workspaceId, body);
  });

  writer = this.handle(async (claims, req) => {
    const { params, body } = workspaceWriterSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.acquireWriter(claims, params.workspaceId, body);
  });

  releaseWriter = this.handle(async (claims, req) => {
    const { params, body } = workspaceWriterReleaseSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.releaseWriter(claims, params.workspaceId);
  });

  rollback = this.handle(async (claims, req) => {
    const { params, body } = workspaceRollbackSchema.parse(req);
    await this.authority.authorizeAttempt(claims, body);
    return this.workspaces.rollback(claims, params.workspaceId, body);
  });

  /** The unwrapped session key, for this lease only: never cached by a proxy. */
  key: Handler = async (req, res, next) => {
    try {
      const claims = attemptClaims(req);
      const { body } = workspaceKeySchema.parse(req);
      await this.authority.authorizeAttempt(claims, body);
      const issued = await this.workspaces.issueKey(claims);
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Cache-Control', 'no-store')
        .json({
          keyId: issued.keyId,
          keyDomain: issued.keyDomain,
          key: issued.key.toString('base64'),
        });
    } catch (error) {
      next(this.refused(req, error));
    }
  };

  private handle(
    work: (
      claims: AttemptClaims,
      req: HarnessAttemptRequest,
    ) => Promise<unknown>,
  ): Handler {
    return async (req, res, next) => {
      try {
        const body = await work(attemptClaims(req), req);
        res.status(HTTP_STATUS.OK).json(body);
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
      const tooLarge = overFileCap(error);
      this.logger.warn(
        'workspace.refused',
        harnessLogMeta('workspace.refused', {
          orgId: req.harnessAttempt?.orgId,
          runId: req.harnessAttempt?.runId,
          reason: tooLarge ? 'workspace_too_large' : 'invalid_request',
          issues: zodIssues(error),
        }),
      );
      return tooLarge
        ? workspaceRefusal(
            new WorkspaceError(
              'WORKSPACE_TOO_LARGE',
              `A workspace holds at most ${String(MAX_WORKSPACE_FILES)} files`,
            ),
          )
        : new BadRequestError('Invalid workspace request');
    }
    if (error instanceof WorkspaceError) {
      // Expected refusals: the service already logged each with its fields.
      return workspaceRefusal(error);
    }
    return transportError(error);
  }
}
