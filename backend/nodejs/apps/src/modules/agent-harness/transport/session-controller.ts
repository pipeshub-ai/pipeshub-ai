import type { IncomingHttpHeaders } from 'http';
import { localeOf } from './request-locale';
import { readUploadedFile } from '../../../libs/utils/multer.utils';
import { NextFunction, Response } from 'express';
import type { RunGates } from '../domain/run-gates';
import { UNTRUSTED_CONTENT_CSP } from '../../../libs/utils/untrusted-content.utils';
import { ZodError } from 'zod';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { SessionRunService } from '../application/session-run-service';
import { SessionInteractionService } from '../application/session-interactions';
import { RunAdmissionService } from '../application/run-admission-service';
import { RunTimelineService } from '../application/run-timeline-service';
import {
  SessionWorkspaceService,
  WorkspaceArchive,
} from '../application/session-workspace-service';
import { zipStream } from '../domain/workspace-zip';
import { trimRun } from '../domain/trim-run';
import { SessionSandboxService } from '../application/session-sandbox-service';
import { CodingEngineService } from '../application/coding-engine-service';
import { SessionRepositoryService } from '../application/session-repository-service';
import { harnessLogMeta, type HarnessLogger } from '../application/harness-log';
import { WorkspaceError } from '../domain/workspace';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import {
  CommitReceipt,
  HarnessRun,
  SANDBOX_COMMAND_PREFIX,
} from '../domain/execution';
import { HarnessMajor, pinnedHarnessMajor } from '../domain/rollout';
import { transportError } from './errors';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessUnavailableError,
} from '../domain/errors';
import { listedEngines } from '../domain/coding-engines';
import { AgentAccessPolicy } from '../ports/agent-access';
import { workspaceRefusal } from './workspace-controller';
import {
  identityString,
  KNOWLEDGE_LIMITED_MESSAGE,
  knowledgeLimited,
  requestPrincipalKind,
  requestVia,
} from './request-identity';
import {
  admissionBodySchema,
  controlSessionRunSchema,
  decideSessionInteractionSchema,
  decideSessionInteractionsSchema,
  listSessionWorkspaceVersionsSchema,
  restoreSessionWorkspaceVersionSchema,
  runSessionCommandSchema,
  putSessionCodingEngineSchema,
  putSessionRepositorySchema,
  uploadFieldsSchema,
  sessionWorkspaceArchiveSchema,
  sessionWorkspaceDiffSchema,
  sessionWorkspaceFileSchema,
  sessionWorkspaceFilesSchema,
} from './schemas';
import { isSafeName } from '../domain/safe-names';

/** The owner's views of a durable session beyond its runs' streams (U3). */
export interface SessionDurableViews {
  timelines?: RunTimelineService;
  workspaces?: SessionWorkspaceService;
  sandbox?: SessionSandboxService;
  codingEngines?: CodingEngineService;
  /** GIT-FLOW (WP-26): the chat's GitHub repository. */
  repositories?: SessionRepositoryService;
  /** Reads an agent's engine profile for `/engine` in its chat, with the caller's own credential. */
  agentAccess?: AgentAccessPolicy;
  logger?: HarnessLogger;
}

const viewError = (error: unknown): unknown =>
  error instanceof WorkspaceError
    ? workspaceRefusal(error)
    : transportError(error);

const IMAGE_SIGNATURES: Array<[string, (b: Buffer) => boolean]> = [
  [
    'image/png',
    (b) =>
      b
        .subarray(0, 8)
        .equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
  ],
  ['image/jpeg', (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff],
  [
    'image/gif',
    (b) =>
      b.subarray(0, 6).toString('latin1') === 'GIF87a' ||
      b.subarray(0, 6).toString('latin1') === 'GIF89a',
  ],
  [
    'image/webp',
    (b) =>
      b.subarray(0, 4).toString('latin1') === 'RIFF' &&
      b.subarray(8, 12).toString('latin1') === 'WEBP',
  ],
];

/** An image by its bytes, never its name; everything else is plain text or opaque bytes, so nothing renders as a page. */
export function servedContentType(bytes: Buffer): string {
  const image = IMAGE_SIGNATURES.find(([, matches]) => matches(bytes));
  if (image) return image[0];
  return bytes.subarray(0, 8000).includes(0)
    ? 'application/octet-stream'
    : 'text/plain; charset=utf-8';
}

/** RFC 5987 `attr-char`: `encodeURIComponent` also leaves `!'()*`, which a header parser may take as syntax. */
const rfc5987 = (value: string): string =>
  encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );

/** SEC-FS-02: the name a download is saved under, one plain file on every system; an unsafe name is reduced. */
export function downloadName(name: string): string {
  if (isSafeName(name)) return name.normalize('NFC');
  let reduced = trimRun(
    name.normalize('NFC').replace(/[\p{Cc}\\/:*?"<>|%]/gu, '_'),
    '. ',
    { end: true },
  );
  if (reduced === '' || reduced === '.' || reduced === '..') reduced = 'file';
  if (!isSafeName(reduced)) reduced = `file_${reduced}`;
  return isSafeName(reduced) ? reduced : 'file';
}

/** Workspace bytes are untrusted content: never sniffed, never scripted, never cached by a proxy. */
export function safeFileHeaders(
  res: Response,
  path: string,
  download: boolean,
): void {
  const name = downloadName(path.split('/').pop() ?? '');
  res
    .setHeader('X-Content-Type-Options', 'nosniff')
    .setHeader('Content-Security-Policy', UNTRUSTED_CONTENT_CSP)
    .setHeader('Cache-Control', 'private, no-store')
    .setHeader(
      'Content-Disposition',
      `${download ? 'attachment' : 'inline'}; filename="${name.replace(/[^\x20-\x7e]|["\\]/g, '_')}"; filename*=UTF-8''${rfc5987(name)}`,
    );
}

interface PublicHarnessRun {
  runId: string;
  sessionId: string;
  agentKey?: string;
  /** The published version of the agent it ran (OWD-14). */
  agentVersion?: number;
  harnessMajor: HarnessMajor;
  profile: HarnessRun['profile'];
  status: HarnessRun['status'];
  outcome: HarnessRun['outcome'];
  /** Which limit cut an answered run's work short (BUDGETEND). */
  truncatedBy?: HarnessRun['truncatedBy'];
  revision: number;
  absoluteDeadlineAt: number;
  /** Queued for a worker that can run it (a mixed-version fleet, RU-2e). */
  waitingForWorker?: { since: number };
  /** The gates the run is held to (G-03..G-05; an answer's include its question's, WP-64). */
  gates?: RunGates;
  /** G11: an operator stopped it: the scope and reason a reloaded chat words ("An admin stopped this: …"). */
  operatorStop?: HarnessRun['operatorStop'];
  createdAt: number;
  updatedAt: number;
}

function publicRun(run: HarnessRun): PublicHarnessRun {
  return {
    runId: run.runId,
    sessionId: run.sessionId,
    ...(run.agentKey !== undefined && run.agentKey.length > 0
      ? { agentKey: run.agentKey }
      : {}),
    ...(run.agentVersion !== undefined
      ? { agentVersion: run.agentVersion }
      : {}),
    harnessMajor: pinnedHarnessMajor(run.harnessMajor),
    profile: run.profile,
    status: run.status,
    outcome: run.outcome,
    ...(run.truncatedBy !== undefined ? { truncatedBy: run.truncatedBy } : {}),
    revision: run.revision,
    absoluteDeadlineAt: run.absoluteDeadlineAt,
    ...(run.status === 'queued' && run.waitingForWorker
      ? { waitingForWorker: { since: run.waitingForWorker.since } }
      : {}),
    ...(run.gates !== undefined ? { gates: structuredClone(run.gates) } : {}),
    ...(run.operatorStop !== undefined
      ? { operatorStop: { ...run.operatorStop } }
      : {}),
    createdAt: run.createdAt,
    updatedAt: run.updatedAt,
  };
}

function publicReceipt(
  receipt: CommitReceipt,
): Omit<CommitReceipt, 'run'> & { run: PublicHarnessRun } {
  return { ...receipt, run: publicRun(receipt.run) };
}

type HarnessSessionKind = 'agent' | 'chat';

function executionScope(
  req: AuthenticatedUserRequest,
  kind: HarnessSessionKind,
): SessionExecutionScope {
  const user = req.user as Record<string, unknown> | undefined;
  const orgId = identityString(user?.['orgId']);
  const actorId = identityString(user?.['userId']);
  if (orgId === null || actorId === null) {
    throw new UnauthorizedError('Authenticated user identity is incomplete');
  }
  return {
    orgId,
    actorId,
    sessionId: req.params['sessionId'] as string,
    ...(kind === 'agent' ? { agentKey: req.params['agentKey'] } : {}),
  };
}

/** The caller's own `Authorization` header, forwarded with an admission; hand-built requests may carry no headers. */
function authorizationOf(req: { headers: IncomingHttpHeaders }): {
  authorization?: string;
} {
  const headers = req.headers as IncomingHttpHeaders | undefined;
  return typeof headers?.authorization === 'string'
    ? { authorization: headers.authorization }
    : {};
}

export class AgentHarnessSessionController {
  constructor(
    private readonly runs: SessionRunService,
    private readonly admissions?: RunAdmissionService,
    private readonly sessionKind: HarnessSessionKind = 'agent',
    private readonly interactions?: SessionInteractionService,
    private readonly views: SessionDurableViews = {},
  ) {}

  /** A run's parts, engine task, approvals, children and spend (U3; owner only). */
  timeline = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.timelines)
        throw new Error('Run timelines are not configured');
      const view = await this.views.timelines.timeline(
        executionScope(req, this.sessionKind),
        req.params['runId'] as string,
      );
      res.status(HTTP_STATUS.OK).json(view);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** A run's spend so far and its status (C10.05; owner only): the chat's live cost while it works. */
  cost = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.timelines)
        throw new Error('Run timelines are not configured');
      const view = await this.views.timelines.cost(
        executionScope(req, this.sessionKind),
        req.params['runId'] as string,
      );
      res.status(HTTP_STATUS.OK).json(view);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** The caller's spend over the conversation (SLASH2 `/usage`). */
  usage = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.timelines)
        throw new Error('Run timelines are not configured');
      const usage = await this.views.timelines.usage(
        executionScope(req, this.sessionKind),
      );
      res.status(HTTP_STATUS.OK).json(usage);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** The person's `/compact [focus]`, saved for the session's next run (SLASH2 C2). */
  compact = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const body = req.body as { focus?: string } | undefined;
      const request = await this.runs.requestCompact(
        executionScope(req, this.sessionKind),
        body?.focus,
      );
      res.status(HTTP_STATUS.ACCEPTED).json({
        requestedAt: request.requestedAt,
        ...(request.focus !== undefined ? { focus: request.focus } : {}),
      });
    } catch (error) {
      next(transportError(error));
    }
  };

  /** What the viewer may decide on the run's approvals (U3). */
  authority = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.interactions)
        throw new Error('Interaction decisions are not configured');
      const authority = await this.interactions.authority(
        executionScope(req, this.sessionKind),
        req.params['runId'] as string,
      );
      res.status(HTTP_STATUS.OK).json(authority);
    } catch (error) {
      next(viewError(error));
    }
  };

  versions = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.workspaces)
        throw new Error('Session workspaces are not configured');
      const { query } = listSessionWorkspaceVersionsSchema
        .pick({ query: true })
        .parse({ query: req.query });
      const versions = await this.views.workspaces.versions(
        executionScope(req, this.sessionKind),
        query,
      );
      res.status(HTTP_STATUS.OK).json(versions);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** The workspace's changes, for `/diff` and `/undo` (SLASH2 C3). */
  diff = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.workspaces)
        throw new Error('Session workspaces are not configured');
      const { query } = sessionWorkspaceDiffSchema
        .pick({ query: true })
        .parse({ query: req.query });
      const diff = await this.views.workspaces.diff(
        executionScope(req, this.sessionKind),
        query.scope,
      );
      res.status(HTTP_STATUS.OK).json(diff);
    } catch (error) {
      next(viewError(error));
    }
  };

  /**
   * SLASH3: the session, within the caller's org, and its owner, before anything reads the body or query
   * (QA-26): anyone else gets 404, never a validation, limit or configuration answer.
   */
  requireOwner = async (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      await this.runs.assertOwner(executionScope(req, this.sessionKind));
      next();
    } catch (error) {
      next(transportError(error));
    }
  };

  /** SLASH3 `/files`: a revision's files under a folder; sizes only. */
  files = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { query } = sessionWorkspaceFilesSchema
        .pick({ query: true })
        .parse({ query: req.query });
      const list = await this.workspaceViews().files(
        executionScope(req, this.sessionKind),
        query,
      );
      res.status(HTTP_STATUS.OK).json(list);
    } catch (error) {
      next(viewError(error));
    }
  };

  /**
   * SLASH3 `/open` and `/download <file>`: the bytes, never as a page. Text is served as text/plain and an image
   * as its sniffed type; HTML and SVG are text. `nosniff` and a sandbox CSP stop a browser from running anything.
   */
  file = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { query } = sessionWorkspaceFileSchema
        .pick({ query: true })
        .parse({ query: req.query });
      const download = query.download === '1';
      const file = await this.workspaceViews().file(
        executionScope(req, this.sessionKind),
        {
          path: query.path,
          ...(query.revision !== undefined ? { revision: query.revision } : {}),
          ...(query.maxBytes !== undefined && !download
            ? { maxBytes: query.maxBytes }
            : {}),
          download,
        },
      );
      safeFileHeaders(res, file.path, download);
      res
        .status(HTTP_STATUS.OK)
        .setHeader('Content-Type', servedContentType(file.bytes))
        .setHeader('X-Workspace-Revision', String(file.revision))
        .setHeader('X-Workspace-File-Size', String(file.sizeBytes))
        .setHeader('X-Workspace-Truncated', file.truncated ? '1' : '0')
        .end(file.bytes);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** SLASH3 `/download [folder]`: a streamed zip; refused before the first byte when too large. */
  archive = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    let archive: WorkspaceArchive;
    try {
      const { query } = sessionWorkspaceArchiveSchema
        .pick({ query: true })
        .parse({ query: req.query });
      archive = await this.workspaceViews().archive(
        executionScope(req, this.sessionKind),
        query,
      );
    } catch (error) {
      next(viewError(error));
      return;
    }
    safeFileHeaders(res, `workspace-v${String(archive.revision)}.zip`, true);
    res
      .status(HTTP_STATUS.OK)
      .setHeader('Content-Type', 'application/zip')
      .setHeader('X-Workspace-Revision', String(archive.revision));
    try {
      for await (const chunk of zipStream(archive.entries)) {
        if (!res.write(chunk)) {
          await new Promise<void>((resolve) => res.once('drain', resolve));
        }
      }
      res.end();
    } catch {
      // Headers are sent: a cut connection is the only way left to say the zip is incomplete.
      res.destroy();
    }
  };

  /** SLASH3 `/upload`: the person's files as one revision of the session workspace. */
  upload = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const fields = uploadFieldsSchema.parse(req.body);
      const files: Express.Multer.File[] = Array.isArray(req.files)
        ? req.files
        : [];
      const paths = JSON.parse(fields.paths) as unknown;
      if (
        !Array.isArray(paths) ||
        paths.length !== files.length ||
        !paths.every((path) => typeof path === 'string')
      ) {
        throw new BadRequestError('paths must name each file once, in order');
      }
      const result = await this.workspaceViews().upload(
        executionScope(req, this.sessionKind),
        {
          baseRevision: fields.baseRevision,
          idempotencyKey: fields.idempotencyKey,
          ...(fields.folder !== undefined ? { folder: fields.folder } : {}),
          replace: fields.replace === 'true',
          files: files.map((file, index) => ({
            path: paths[index] as string,
            mimeType: file.mimetype || 'application/octet-stream',
            sizeBytes: file.size,
            read: () => readUploadedFile(file),
          })),
        },
      );
      res.status(HTTP_STATUS.OK).json(result);
    } catch (error) {
      next(
        error instanceof SyntaxError || error instanceof ZodError
          ? new BadRequestError(
              error instanceof ZodError
                ? 'Invalid upload fields'
                : 'paths is not JSON',
            )
          : viewError(error),
      );
    }
  };

  /**
   * SLASH3 `/engine`: this chat's coding engine from its next message (SD5), checked as a person's save is and
   * written as one audited change. A running reply keeps its pin.
   */
  setCodingEngine = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.codingEngines)
        throw new Error('Coding engines are not configured');
      const scope = executionScope(req, this.sessionKind);
      const { engine } = putSessionCodingEngineSchema.shape.body.parse(
        req.body,
      );
      const agentEngines =
        engine === null ? undefined : await this.agentEngines(req, scope);
      res
        .status(HTTP_STATUS.OK)
        .json(
          await this.views.codingEngines.setSessionChoice(
            scope.orgId,
            scope.actorId,
            scope.sessionId,
            engine,
            ...(agentEngines !== undefined ? [agentEngines] : []),
          ),
        );
    } catch (error) {
      next(transportError(error));
    }
  };

  /** GIT-FLOW (WP-26): the chat's repository and its state, for the chip; 204 when it has none. */
  repository = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const view = await this.repositoryViews().view(
        executionScope(req, this.sessionKind),
      );
      if (view === null) res.status(HTTP_STATUS.NO_CONTENT).end();
      else res.status(HTTP_STATUS.OK).json(view);
    } catch (error) {
      next(transportError(error));
    }
  };

  /**
   * GIT-FLOW: the owner picks the chat's GitHub repository (the picker), checked (GitHub only, a coding engine, a
   * GitHub connection the person may use, read with their own credential) and written as one audited change.
   */
  setRepository = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const repositories = this.repositoryViews();
      const scope = executionScope(req, this.sessionKind);
      const body = putSessionRepositorySchema.shape.body.parse(req.body);
      const authorization = req.headers.authorization;
      if (typeof authorization !== 'string') throw new HarnessForbiddenError();
      const agentEngines = await this.agentEngines(req, scope);
      res.status(HTTP_STATUS.OK).json(
        await repositories.set(
          scope,
          {
            orgId: scope.orgId,
            actorId: scope.actorId,
            authorization,
            ...(agentEngines !== undefined ? { agentEngines } : {}),
          },
          {
            url: body.url,
            toolsetInstanceId: body.toolsetInstanceId,
            ...(body.baseRef !== undefined ? { baseRef: body.baseRef } : {}),
          },
        ),
      );
    } catch (error) {
      next(transportError(error));
    }
  };

  /** GIT-FLOW: the chat works on no repository from its next message; refused once its workspace holds the clone. */
  clearRepository = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      await this.repositoryViews().clear(executionScope(req, this.sessionKind));
      res.status(HTTP_STATUS.NO_CONTENT).end();
    } catch (error) {
      next(transportError(error));
    }
  };

  private repositoryViews(): SessionRepositoryService {
    if (!this.views.repositories)
      throw new HarnessUnavailableError('Chat repositories are not configured');
    return this.views.repositories;
  }

  /**
   * The engines an agent chat's agent lists (CMDUI): its turn honours only one of them, so `/engine` may save
   * only one of them. Undefined in the default chat, or where agents cannot be read here.
   */
  private async agentEngines(
    req: AuthenticatedUserRequest,
    scope: SessionExecutionScope,
  ): Promise<string[] | undefined> {
    const access = this.views.agentAccess;
    if (scope.agentKey === undefined || access === undefined) return undefined;
    const authorization = req.headers.authorization;
    if (typeof authorization !== 'string') throw new HarnessForbiddenError();
    const definition = await access.authorizeRun({
      orgId: scope.orgId,
      actorId: scope.actorId,
      agentKey: scope.agentKey,
      authorization,
    });
    if (definition === null) throw new HarnessForbiddenError();
    return listedEngines(definition.engineProfile);
  }

  /** SLASH3 `/sandbox`: what runs this chat's code; starts nothing. */
  sandbox = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.sandbox)
        throw new Error('Session sandbox is not configured');
      res
        .status(HTTP_STATUS.OK)
        .json(
          await this.views.sandbox.view(executionScope(req, this.sessionKind)),
        );
    } catch (error) {
      next(viewError(error));
    }
  };

  /** SLASH3 "End now": ends a kept sign-in sandbox; ending none is the same answer. */
  endKeptSandbox = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.sandbox)
        throw new Error('Session sandbox is not configured');
      await this.views.sandbox.endKept(executionScope(req, this.sessionKind));
      res.status(HTTP_STATUS.NO_CONTENT).end();
    } catch (error) {
      next(viewError(error));
    }
  };

  private workspaceViews(): SessionWorkspaceService {
    if (!this.views.workspaces)
      throw new Error('Session workspaces are not configured');
    return this.views.workspaces;
  }

  restore = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.views.workspaces)
        throw new Error('Session workspaces are not configured');
      const body = restoreSessionWorkspaceVersionSchema.shape.body.parse(
        req.body,
      );
      const restored = await this.views.workspaces.restore(
        executionScope(req, this.sessionKind),
        {
          revision: Number(req.params['revision']),
          baseRevision: body.baseRevision,
          idempotencyKey: body.idempotencyKey,
        },
      );
      res.status(HTTP_STATUS.OK).json(restored);
    } catch (error) {
      next(viewError(error));
    }
  };

  /** Approve or deny an interaction the run waits on (P2.6c). */
  decide = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.interactions)
        throw new Error('Interaction decisions are not configured');
      const body = decideSessionInteractionSchema.shape.body.parse(req.body);
      const decided = await this.interactions.decide({
        ...executionScope(req, this.sessionKind),
        runId: req.params['runId'] as string,
        interactionId: req.params['interactionId'] as string,
        decision: body.decision,
        idempotencyKey: body.idempotencyKey,
        channel: 'web',
        ...(body.scope === 'conversation'
          ? { scope: 'conversation' as const }
          : {}),
        ...(body.reason !== undefined ? { reason: body.reason } : {}),
        ...(body.edits !== undefined ? { edits: body.edits } : {}),
        ...(body.remember === true ? { remember: true } : {}),
        ...(body.remember === true && req.headers.authorization !== undefined
          ? { authorization: req.headers.authorization }
          : {}),
      });
      res.status(HTTP_STATUS.OK).json({
        receipt:
          decided.receipt === null ? null : publicReceipt(decided.receipt),
        interaction: decided.interaction,
      });
    } catch (error) {
      next(transportError(error));
    }
  };

  /** One decision on several of the run's interactions, "Allow all N" (DECIDE409). */
  decideAll = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.interactions)
        throw new Error('Interaction decisions are not configured');
      const body = decideSessionInteractionsSchema.shape.body.parse(req.body);
      const decided = await this.interactions.decideAll({
        ...executionScope(req, this.sessionKind),
        runId: req.params['runId'] as string,
        interactionIds: body.interactionIds,
        decision: body.decision,
        idempotencyKey: body.idempotencyKey,
        channel: 'web',
        ...(body.scope === 'conversation'
          ? { scope: 'conversation' as const }
          : {}),
        ...(body.remember === true ? { remember: true } : {}),
        ...(body.reason !== undefined ? { reason: body.reason } : {}),
      });
      res.status(HTTP_STATUS.OK).json({
        receipt:
          decided.receipt === null ? null : publicReceipt(decided.receipt),
        interactions: decided.interactions,
      });
    } catch (error) {
      next(transportError(error));
    }
  };

  createSessionRun = this.admit(false);
  submitSessionRun = this.admit(true);

  get = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const run = await this.runs.get(
        executionScope(req, this.sessionKind),
        req.params['runId'] as string,
      );
      const effectivePolicy = await this.runs.effectivePolicy(run);
      res.status(HTTP_STATUS.OK).json({ run: publicRun(run), effectivePolicy });
    } catch (error) {
      next(transportError(error));
    }
  };

  /** `GET …/runs/{runId}/resolved` (CPD-5): the run's pin, for the run `get` authorizes. */
  resolved = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const run = await this.runs.get(
        executionScope(req, this.sessionKind),
        req.params['runId'] as string,
      );
      const view = await this.runs.resolved(run);
      if (view.resolved === null) {
        this.views.logger?.debug(
          'run.resolved_unpinned',
          harnessLogMeta('run.resolved_unpinned', {
            orgId: run.orgId,
            sessionId: run.sessionId,
            runId: run.runId,
          }),
        );
      }
      res.status(HTTP_STATUS.OK).json(view);
    } catch (error) {
      next(transportError(error));
    }
  };

  cancel = this.control('cancel');
  resume = this.control('resume');

  private control(
    action: 'cancel' | 'resume',
  ): (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void> {
    return async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        const body = controlSessionRunSchema.shape.body.parse(req.body);
        const receipt = await this.runs.control({
          ...executionScope(req, this.sessionKind),
          runId: req.params['runId'] as string,
          expectedRevision: body.expectedRevision,
          idempotencyKey: body.idempotencyKey,
          action,
        });
        res.status(HTTP_STATUS.OK).json({ receipt: publicReceipt(receipt) });
      } catch (error) {
        next(transportError(error));
      }
    };
  }

  /**
   * SLASH3 `/run`: a run of its own kind on the session, admitted like a turn (owner, quotas, one active run),
   * whose worker makes no model call and runs the line through the agent's `run_code` (§3.5).
   */
  runCommand = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (!this.admissions)
        throw new Error('Durable run admission is not configured');
      if (knowledgeLimited(req))
        throw new HarnessForbiddenError(KNOWLEDGE_LIMITED_MESSAGE);
      const scope = executionScope(req, this.sessionKind);
      const body = runSessionCommandSchema.shape.body.parse(req.body);
      const outcome = await this.admissions.submit({
        orgId: scope.orgId,
        actorId: scope.actorId,
        ...(scope.agentKey !== undefined ? { agentKey: scope.agentKey } : {}),
        sessionId: scope.sessionId,
        ...authorizationOf(req),
        via: requestVia(req),
        principalKind: requestPrincipalKind(req),
        idempotencyKey: body.idempotencyKey,
        deadlineMs: RELEASE_DEFAULTS.runs.deadlineMs,
        input: {
          query: `${SANDBOX_COMMAND_PREFIX}${body.command}`,
          recordIds: [],
          filters: {},
          attachments: [],
          ...(body.tools !== undefined ? { tools: body.tools } : {}),
          ...(body.agentCapabilities !== undefined
            ? { agentCapabilities: body.agentCapabilities }
            : {}),
          model: {
            chatMode: body.chatMode,
            ...(body.modelKey !== undefined ? { modelKey: body.modelKey } : {}),
            ...(body.modelName !== undefined
              ? { modelName: body.modelName }
              : {}),
            ...(body.modelProvider !== undefined
              ? { modelProvider: body.modelProvider }
              : {}),
            ...(body.modelFriendlyName !== undefined
              ? { modelFriendlyName: body.modelFriendlyName }
              : {}),
          },
          sandboxCommand: { timeoutS: body.timeoutS },
        },
      });
      if (outcome.route === 'legacy') {
        throw new HarnessConflictError(
          'SPEC_UNSUPPORTED',
          'Sandbox commands need the new harness',
        );
      }
      const result = outcome.admission;
      res.status(HTTP_STATUS.ACCEPTED).json({
        admission: { ...result, run: publicRun(result.run) },
      });
    } catch (error) {
      next(transportError(error));
    }
  };

  private admit(
    existingSession: boolean,
  ): (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void> {
    return async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!this.admissions)
          throw new Error('Durable run admission is not configured');
        if (knowledgeLimited(req))
          throw new HarnessForbiddenError(KNOWLEDGE_LIMITED_MESSAGE);
        const user = req.user as Record<string, unknown> | undefined;
        const orgId = identityString(user?.['orgId']);
        const actorId = identityString(user?.['userId']);
        if (orgId === null || actorId === null) {
          throw new UnauthorizedError(
            'Authenticated user identity is incomplete',
          );
        }
        const body = admissionBodySchema.parse(req.body);
        if (existingSession && body.repository !== undefined) {
          throw new BadRequestError(
            "A conversation's repository changes only with PUT …/repository",
          );
        }
        if (!existingSession && body.answers !== undefined) {
          throw new BadRequestError(
            'A new conversation answers no question: send `answers` only to an existing one',
          );
        }
        // G-13: an answer from the inbox continues on the asking run's model, as the chat's own card would.
        const asked =
          body.answers !== undefined
            ? (
                await this.runs.get(
                  executionScope(req, this.sessionKind),
                  body.answers,
                )
              ).input
            : undefined;
        const tools = body.tools ?? asked?.tools;
        const agentCapabilities =
          body.agentCapabilities ?? asked?.agentCapabilities;
        const model =
          asked?.model !== undefined && body.modelKey === undefined
            ? asked.model
            : {
                ...(body.modelKey !== undefined
                  ? { modelKey: body.modelKey }
                  : {}),
                ...(body.modelName !== undefined
                  ? { modelName: body.modelName }
                  : {}),
                ...(body.modelProvider !== undefined
                  ? { modelProvider: body.modelProvider }
                  : {}),
                ...(body.modelFriendlyName !== undefined
                  ? { modelFriendlyName: body.modelFriendlyName }
                  : {}),
                ...(body.reasoningEffort !== undefined
                  ? { reasoningEffort: body.reasoningEffort }
                  : {}),
                ...(body.modelAccountId !== undefined
                  ? { accountId: body.modelAccountId }
                  : {}),
                chatMode: body.chatMode,
              };
        const outcome = await this.admissions.submit({
          orgId,
          actorId,
          ...(this.sessionKind === 'agent'
            ? { agentKey: req.params['agentKey'] }
            : {}),
          ...(existingSession ? { sessionId: req.params['sessionId'] } : {}),
          ...authorizationOf(req),
          via: requestVia(req),
          principalKind: requestPrincipalKind(req),
          ...localeOf(req),
          ...(body.steer === true ? { steerOnly: true } : {}),
          // WP-64: admission holds the answer to the asking run's gates and budget.
          ...(body.answers !== undefined ? { answers: body.answers } : {}),
          ...(body.repository !== undefined
            ? { repository: body.repository }
            : {}),
          idempotencyKey: body.idempotencyKey,
          deadlineMs: body.deadlineMs,
          ...(body.regenerate !== undefined
            ? { regenerate: { messageId: body.regenerate.messageId } }
            : {}),
          input: {
            query: body.query,
            recordIds: body.recordIds,
            filters: body.filters,
            ...(body.appliedFilters !== undefined
              ? { appliedFilters: body.appliedFilters }
              : {}),
            attachments: body.attachments,
            ...(body.inputArtifacts !== undefined &&
            body.inputArtifacts.length > 0
              ? { inputArtifacts: body.inputArtifacts }
              : {}),
            ...(tools !== undefined ? { tools } : {}),
            ...(agentCapabilities !== undefined ? { agentCapabilities } : {}),
            model,
            ...(body.timezone !== undefined ? { timezone: body.timezone } : {}),
            ...(body.currentTime !== undefined
              ? { currentTime: body.currentTime }
              : {}),
          },
        });
        if (outcome.route === 'legacy') {
          // Nothing was written; the client submits this turn on the legacy path.
          res
            .status(HTTP_STATUS.OK)
            .json({ admission: null, rollout: outcome.rollout });
          return;
        }
        const result = outcome.admission;
        res.status(HTTP_STATUS.ACCEPTED).json({
          admission: { ...result, run: publicRun(result.run) },
        });
      } catch (error) {
        next(transportError(error));
      }
    };
  }
}
