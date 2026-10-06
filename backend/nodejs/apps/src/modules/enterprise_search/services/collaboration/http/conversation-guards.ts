import { NextFunction, RequestHandler, Response } from 'express';
import { injectable } from 'inversify';
import { Types } from 'mongoose';
import {
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { Logger } from '../../../../../libs/services/logger.service';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import {
  Decision,
  IAuthorizationService,
  IScopedChatLoader,
  ScopedLoadedChat,
} from '../../../../authz/ports';
import { IProjectAccessPort } from '../../../../authz/ports/project-access.port';
import { atLeast } from '../../../../authz/domain/ladder';
import { TeamIds } from '../../../../authz/domain/types';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { IProjectDocument } from '../../../../projects/types/project.interfaces';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import {
  OPERATION_REQUIREMENTS,
  OperationContext,
  toAccessView,
  toConversationRole,
} from '../access/conversation-access.policy';
import { errorForCode } from '../access/conversation-access.authorizer';
import {
  ArchiveScope,
  readFilter,
  writeFilter,
} from '../access/conversation-access.filters';
import {
  COLLAB_ERROR_CODES,
  ConnectorSetupRequiredError,
  ConversationNotFoundError,
  ConversationReadOnlyError,
  OwnerInactiveError,
  ProjectAccessRequiredError,
  TeamResolutionUnavailableError,
} from '../domain/errors';
import {
  Caller,
  ConversationOperation,
  ConversationRef,
} from '../domain/types';
import { IRunLeaseManager, LeaseHandle } from '../leases/lease.types';
import { IMentionTurnGate } from '../mentions/mention-turn-gate';
import { IAgentReadinessPort } from '../readiness/agent-readiness.port';
import { TurnLifecycle } from '../turn/turn-lifecycle';
import {
  ConversationAccessGrant,
  conversationContextOf,
  conversationGrantOf,
  setConversationContext,
  takeUnclaimedLease,
  trackLease,
} from './conversation-context';
import { decideChatAccess, resolveCallerTeams } from './decide-access';
import { OwnerActivity } from './owner-activity';
import { assertResumeAllowed, ResumeBody } from './resume-binding';
import {
  assertNotChangedSince,
  assertNotDuplicate,
} from './turn-preconditions';

export type GuardKind = ConversationRef['kind'];
/** `'any'` is a list over both kinds. */
export type ListKind = GuardKind | 'any';

export type GuardMarkValue =
  | { readonly op: ConversationOperation; readonly kind: GuardKind }
  | { readonly lease: true; readonly kind: GuardKind }
  | { readonly list: true; readonly kind: ListKind }
  | { readonly caller: true };

/** Lets the route-table test read what a layer guards from `router.stack`. */
export const GUARD_MARK = Symbol.for('collab.conversationGuard');

export function guardMarkOf(handler: unknown): GuardMarkValue | undefined {
  return (handler as { [GUARD_MARK]?: GuardMarkValue })[GUARD_MARK];
}

/** The flag is passed so an option can differ with it on, for surfaces whose flag-off output must not change. */
type PerRequest =
  | boolean
  | ((req: AuthenticatedUserRequest, collab: boolean) => boolean);

export interface ListScopeOptions {
  includeOwned?: PerRequest;
  includeShared?: PerRequest;
  /** Whether chats shared to the caller's projects are listed; archive surfaces list them only for chats the caller archived per user (flag on). */
  includeProjects?: PerRequest;
  /** `'collabOnly'` lists `sharedWith` rows only with the flag on (the project list never did with it off); `'never'` lists owned and project chats only. */
  shareRows?: 'always' | 'collabOnly' | 'never';
  /** Per-user archive scope; ignored with the flag off, where archive is the global `isArchived`. */
  archived?: ArchiveScope;
  /** Also builds `sharedListFilter`: chats shared to the caller by `sharedWith` rows only. */
  sharedWithMeList?: boolean;
}

const resolveOption = (
  option: PerRequest | undefined,
  req: AuthenticatedUserRequest,
  collab: boolean,
): boolean =>
  option === undefined
    ? true
    : typeof option === 'boolean'
      ? option
      : option(req, collab);

/** A resume is a send whose body answers a card; `runLease()` binds it to the card's requester. */
export type LeaseOperation = Extract<
  ConversationOperation,
  'send' | 'regenerate'
>;

export interface RunLeaseOptions {
  /** `regenerate` skips the `baseSeq` and duplicate steps: it adds no message. Default `send`. */
  op?: LeaseOperation;
}

export interface ConversationGuardsDeps {
  authz: IAuthorizationService;
  chats: IScopedChatLoader;
  projects: IProjectAccessPort;
  flags: IFeatureFlags;
  users: IUserDirectory;
  teams: ITeamDirectory;
  /** Needed by `runLease()` with the flag on. */
  leases?: IRunLeaseManager;
  readiness?: IAgentReadinessPort;
  /** Validates and classifies a send's mentions; absent, mentions are ignored. */
  mentions?: IMentionTurnGate;
  logger?: Pick<Logger, 'warn'>;
  clock?: IClock;
}

const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i;
const REGENERATE_TAIL_MESSAGES = 50;

const defaultLogger = Logger.getInstance({ service: 'ConversationGuards' });

const mark = <T extends RequestHandler>(handler: T, value: GuardMarkValue): T =>
  Object.defineProperty(handler, GUARD_MARK, { value });

interface TailMessage {
  _id: Types.ObjectId;
  messageType: string;
  authorUserId?: Types.ObjectId;
}

/** Route middleware that authorizes a conversation request and stores a `ConversationRequestContext`. Errors go to `next(err)`, before any SSE header. */
@injectable()
export class ConversationGuards {
  private readonly ownerActivity: OwnerActivity;

  constructor(private readonly deps: ConversationGuardsDeps) {
    this.ownerActivity = new OwnerActivity(
      deps.users,
      deps.logger ?? defaultLogger,
      deps.clock ?? systemClock,
    );
  }

  authorize(op: ConversationOperation, kind: GuardKind): RequestHandler {
    return mark(
      this.guard((req) => this.authorizeRequest(req, op, kind)),
      { op, kind },
    );
  }

  /**
   * `authorize` for a conversation named somewhere other than the route's path, such as a request body.
   * Throws the same not-found as the middleware; returns the grant it stores on the request.
   */
  async authorizeById(
    req: AuthenticatedUserRequest,
    op: ConversationOperation,
    kind: GuardKind,
    conversationId: string,
  ): Promise<ConversationAccessGrant> {
    await this.authorizeRequest(req, op, kind, conversationId);
    return conversationGrantOf(req);
  }

  /**
   * Turn preconditions, in order, first failure wins: resume binding, `baseSeq`, duplicate
   * `clientMessageId`, mentions (a note is refused here, before it can take a lease), agent readiness, project access, then the lease. Mount after `authorize('send'|'regenerate')`
   * and before any SSE header. Pass-through with the flag off.
   */
  runLease(kind: GuardKind, options: RunLeaseOptions = {}): RequestHandler {
    const op = options.op ?? 'send';
    return mark(
      async (
        req: AuthenticatedUserRequest,
        res: Response,
        next: NextFunction,
      ): Promise<void> => {
        let lease: LeaseHandle | undefined;
        try {
          lease = await this.acquireTurnLease(req, kind, op);
        } catch (error) {
          next(error);
          return;
        }
        if (lease) {
          trackLease(lease);
          const release = (): void => {
            if (takeUnclaimedLease(lease)) {
              void new TurnLifecycle(lease).settle('unstarted');
            }
          };
          if (res.writableEnded || res.destroyed) {
            release();
            return;
          }
          res.once('close', release);
        }
        next();
      },
      { lease: true, kind },
    );
  }

  listScope(kind: ListKind, options: ListScopeOptions = {}): RequestHandler {
    return mark(
      this.guard(async (req) => {
        const base = this.identityOf(req);
        const collab = await this.collabEnabled();
        const includeOwned = resolveOption(options.includeOwned, req, collab);
        const includeShared = resolveOption(options.includeShared, req, collab);
        const needsProjects =
          includeShared && resolveOption(options.includeProjects, req, collab);
        // Project membership by team applies with the flag off too, as on the detail route; chat share rows from teams do not.
        const resolvedTeams =
          needsProjects || (collab && includeShared)
            ? await this.resolveTeams(req)
            : [];
        const accessibleProjectIds = needsProjects
          ? await this.deps.projects.accessibleProjectIds({
              ...base,
              teamIds: resolvedTeams,
            })
          : [];
        const caller: Caller = {
          ...base,
          teamIds: collab ? resolvedTeams : [],
        };
        const target = { kind, agentKey: req.params.agentKey };
        const archived = collab ? options.archived : undefined;
        const listFilter = readFilter(caller, target, {
          accessibleProjectIds,
          includeOwned,
          includeShared,
          includeShareRows:
            options.shareRows === 'never'
              ? false
              : options.shareRows !== 'collabOnly' || collab,
          archived,
          excludeHidden: collab,
        });
        const sharedListFilter = options.sharedWithMeList
          ? readFilter(caller, target, {
              accessibleProjectIds: [],
              includeOwned: false,
              includeShared: true,
              archived,
              excludeHidden: collab,
            })
          : undefined;
        setConversationContext(req, {
          caller,
          listFilter,
          sharedListFilter,
          accessibleProjectIds,
          collab,
        });
      }),
      { list: true, kind },
    );
  }

  /** New conversations: there is no session yet, so teams are not resolved. */
  caller(): RequestHandler {
    return mark(
      this.guard(async (req) => {
        const { userId, orgId } = this.identityOf(req);
        setConversationContext(req, {
          caller: { userId, orgId, teamIds: 'unresolved' },
          collab: await this.collabEnabled(),
        });
      }),
      { caller: true },
    );
  }

  private async acquireTurnLease(
    req: AuthenticatedUserRequest,
    kind: GuardKind,
    op: LeaseOperation,
  ): Promise<LeaseHandle | undefined> {
    const ctx = conversationContextOf(req);
    if (ctx.collab !== true) {
      return undefined;
    }
    const { leases, readiness } = this.deps;
    if (!leases || !readiness) {
      throw new InternalServerError('run lease dependencies not configured');
    }
    const grant = conversationGrantOf(req);
    const { session, caller } = grant;
    const sessionId = session._id.toString();
    const scope = {
      sessionId: session._id,
      orgId: new Types.ObjectId(caller.orgId),
      callerId: caller.userId,
      ownerId: session.userId.toString(),
    };
    const body = (req.body ?? {}) as ResumeBody & {
      baseSeq?: number;
      clientMessageId?: string;
    };
    if (op !== 'regenerate') {
      const card = await assertResumeAllowed(scope, session, body);
      if (card !== undefined) {
        // A text answer is forwarded as the resume it is, once bound to its card.
        (req.body as ResumeBody).resume = { toolCallMessageId: card };
      }
      if (body.baseSeq !== undefined) {
        await assertNotChangedSince(scope, body.baseSeq);
      }
      if (body.clientMessageId !== undefined) {
        await assertNotDuplicate(scope, body.clientMessageId);
      }
      if (card === undefined && body.resume === undefined) {
        await this.deps.mentions?.admit(req, grant, kind);
      }
    }

    const agentKey = kind === 'agent' ? req.params.agentKey : undefined;
    // A `tools` selection narrows the toolsets the AI backend checks; readiness checks them all and
    // could refuse a send the backend would run, so the backend's own check decides then.
    const toolSelection = Array.isArray(
      (req.body as { tools?: unknown }).tools,
    );
    if (
      agentKey !== undefined &&
      req.user?.isServiceAccount !== true &&
      !toolSelection
    ) {
      const ready = await readiness.check(
        { orgId: caller.orgId, userId: caller.userId },
        agentKey,
      );
      if (ready.status === 'blocked') {
        throw new ConnectorSetupRequiredError(ready.toolsets);
      }
    }

    const project = session.projectId
      ? await this.requireProject(req, caller, session.projectId.toString())
      : undefined;

    const editableProjectIds =
      session.projectId &&
      grant.via.some((p) => p.type === 'project' && atLeast(p.role, 'editor'))
        ? [session.projectId.toString()]
        : [];
    const filter = writeFilter(
      caller,
      { kind, agentKey, conversationId: sessionId },
      { collab: true, editableProjectIds },
    );
    let lease: LeaseHandle;
    try {
      lease = await leases.acquire(sessionId, caller, filter);
    } catch (error) {
      throw error instanceof ConversationNotFoundError
        ? await this.missedAcquire(
            req,
            caller,
            sessionId,
            kind,
            agentKey,
            error,
          )
        : error;
    }
    setConversationContext(req, { ...ctx, ...(project && { project }), lease });
    return lease;
  }

  /** The guard proved write access, so a miss means access changed since: read-only is 403, anything less stays 404. */
  private async missedAcquire(
    req: AuthenticatedUserRequest,
    caller: Caller,
    sessionId: string,
    kind: GuardKind,
    agentKey: string | undefined,
    notFound: ConversationNotFoundError,
  ): Promise<Error> {
    try {
      const loaded = await this.deps.chats.loadScoped(caller.orgId, {
        id: sessionId,
        kind,
        agentKey,
      });
      if (!loaded) {
        return notFound;
      }
      const { decision } = await this.decide(
        req,
        { userId: caller.userId, orgId: caller.orgId },
        'read',
        kind,
        loaded,
        true,
        undefined,
      );
      return decision.allow ? new ConversationReadOnlyError() : notFound;
    } catch {
      return notFound;
    }
  }

  /** The sender needs project access themself, even when the owner linked the project (D7). */
  private async requireProject(
    req: AuthenticatedUserRequest,
    caller: Caller,
    projectId: string,
  ): Promise<IProjectDocument> {
    const attempt = (teamIds: TeamIds): Promise<IProjectDocument> =>
      this.deps.projects.assertAtLeast(
        { ...caller, teamIds },
        projectId,
        'viewer',
      );
    const denied = (error: unknown): boolean =>
      error instanceof NotFoundError || error instanceof ForbiddenError;
    try {
      return await attempt(caller.teamIds);
    } catch (error) {
      if (!denied(error)) {
        throw error;
      }
      if (caller.teamIds === 'unresolved') {
        const teamIds = await this.resolveTeams(req);
        if (teamIds === 'unresolved') {
          throw new TeamResolutionUnavailableError();
        }
        try {
          return await attempt(teamIds);
        } catch (retry) {
          if (!denied(retry)) {
            throw retry;
          }
        }
      }
      throw new ProjectAccessRequiredError(projectId);
    }
  }

  private guard(
    run: (req: AuthenticatedUserRequest) => void | Promise<void>,
  ): RequestHandler {
    return async (
      req: AuthenticatedUserRequest,
      _res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        await run(req);
        next();
      } catch (error) {
        next(error);
      }
    };
  }

  private async authorizeRequest(
    req: AuthenticatedUserRequest,
    op: ConversationOperation,
    kind: GuardKind,
    id: string | undefined = req.params.conversationId,
  ): Promise<void> {
    const { userId, orgId } = this.identityOf(req);
    const agentKey = kind === 'agent' ? req.params.agentKey : undefined;
    if (id === undefined || !OBJECT_ID_HEX.test(id)) {
      throw new ConversationNotFoundError();
    }
    const loaded = await this.deps.chats.loadScoped(orgId, {
      id,
      kind,
      agentKey,
    });
    if (!loaded) {
      throw new ConversationNotFoundError();
    }
    const collab = await this.collabEnabled();
    const ownerId = loaded.session.userId.toString();
    const operation: OperationContext | undefined =
      collab && op === 'regenerate'
        ? {
            isAuthorOfAnsweredQuestion: await this.isAuthorOfAnsweredQuestion(
              loaded,
              req.params.messageId,
              userId,
            ),
          }
        : undefined;

    const { decision, teamIds } = await this.decide(
      req,
      { userId, orgId },
      op,
      kind,
      loaded,
      collab,
      operation,
    );
    if (!decision.allow) {
      throw errorForCode(
        collab
          ? (decision.code ?? COLLAB_ERROR_CODES.NOT_FOUND)
          : COLLAB_ERROR_CODES.NOT_FOUND,
      );
    }

    const isOwner = ownerId === userId;
    const ownerActive =
      collab &&
      OPERATION_REQUIREMENTS[op].requiresActiveOwner === true &&
      !isOwner
        ? await this.ownerActivity.isActive(orgId, ownerId)
        : undefined;
    if (ownerActive === false) {
      throw new OwnerInactiveError();
    }

    const caller: Caller = { userId, orgId, teamIds };
    const role = toConversationRole(decision.role);
    if (role === 'none') {
      throw new ConversationNotFoundError();
    }
    setConversationContext(req, {
      caller,
      collab,
      grant: {
        session: loaded.session,
        role,
        via: decision.via,
        caller,
        view: toAccessView(role, loaded.session, caller, {
          collab,
          ownerActive,
        }),
      },
    });
  }

  /** Teams are resolved only when the first pass says a team grant could decide the outcome. */
  private async decide(
    req: AuthenticatedUserRequest,
    identity: { userId: string; orgId: string },
    op: ConversationOperation,
    kind: GuardKind,
    loaded: ScopedLoadedChat,
    collab: boolean,
    operation: OperationContext | undefined,
  ): Promise<{ decision: Decision; teamIds: TeamIds }> {
    return decideChatAccess({
      authz: this.deps.authz,
      identity,
      op,
      loaded,
      collab,
      context: {
        request: req,
        requestId: this.requestIdOf(req),
        ...(operation && { operation }),
        ...(!collab && { legacyKind: kind }),
      },
      resolveTeams: () => this.resolveTeams(req),
    });
  }

  private resolveTeams(req: AuthenticatedUserRequest): Promise<TeamIds> {
    return resolveCallerTeams(this.deps.teams, req);
  }

  /** Legacy rows carry no `authorUserId` and read as authored by the owner (O-1). */
  private async isAuthorOfAnsweredQuestion(
    loaded: ScopedLoadedChat,
    messageId: string | undefined,
    userId: string,
  ): Promise<boolean> {
    const ownerId = loaded.session.userId.toString();
    if (messageId === undefined || !OBJECT_ID_HEX.test(messageId)) {
      return userId === ownerId;
    }
    const tail = await ChatSessionMessage.find({
      sessionId: loaded.session._id,
      orgId: loaded.session.orgId,
    })
      .sort({ seq: -1 })
      .limit(REGENERATE_TAIL_MESSAGES)
      .select('messageType authorUserId')
      .lean<TailMessage[]>();
    const answerIdx = tail.findIndex((m) => m.messageType === 'bot_response');
    const answer = tail[answerIdx];
    const question =
      answer?._id.toString() === messageId
        ? tail.slice(answerIdx + 1).find((m) => m.messageType === 'user_query')
        : undefined;
    return (question?.authorUserId?.toString() ?? ownerId) === userId;
  }

  private identityOf(req: AuthenticatedUserRequest): {
    userId: string;
    orgId: string;
  } {
    const { userId, orgId } = callerIdentityOf(req);
    if (userId === '' || orgId === '') {
      throw new UnauthorizedError('Authentication required');
    }
    return { userId, orgId };
  }

  private requestIdOf(req: AuthenticatedUserRequest): string {
    return req.context?.requestId ?? '';
  }

  private collabEnabled(): Promise<boolean> {
    return this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats);
  }
}
