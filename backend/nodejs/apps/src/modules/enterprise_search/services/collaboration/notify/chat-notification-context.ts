import { Types } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { Logger } from '../../../../../libs/services/logger.service';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { ProjectFacts, TeamIds } from '../../../../authz/domain/types';
import { projectFactsOf } from '../../../../authz/loaders/project.loader';
import {
  IAuthorizationService,
  ScopedLoadedChat,
  ScopedSession,
} from '../../../../authz/ports';
import { decideChatAccess, resolveCallerTeams } from '../http/decide-access';
import { Project } from '../../../../projects/schema/project.schema';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { ChatSession } from '../../../schema/chat.session.schema';

export const CHAT_TITLE_MAX_CHARS = 80;

/** What the list adds to a stored chat notification; nothing here is persisted (F-12). */
export interface NotificationContext {
  chatTitle?: string;
  actorName?: string;
}

export interface ContextRow {
  type: string;
  payload?: Record<string, unknown>;
}

export type SessionWithTitle = ScopedSession & { title?: string };

/** One org-scoped read of the non-deleted sessions a page refers to. */
export type SessionBatchLoader = (
  orgId: string,
  sessionIds: readonly string[],
) => Promise<readonly SessionWithTitle[]>;

export interface IChatNotificationContext {
  /**
   * Context per row index, for `chat.*` rows (except `chat.deleted`) whose chat the caller can still
   * read. Empty with the flag off. Never throws: a failure leaves the rows on their generic text.
   */
  resolve(
    req: AuthenticatedUserRequest,
    rows: readonly ContextRow[],
  ): Promise<ReadonlyMap<number, NotificationContext>>;
}

const SESSION_FIELDS =
  'orgId userId isDeleted sharedWith projectId projectVisibility settings aclVersion initiator isArchived agentKey title';

export const loadSessionsForNotifications: SessionBatchLoader = (
  orgId,
  sessionIds,
) =>
  ChatSession.find({
    _id: { $in: sessionIds.map((id) => new Types.ObjectId(id)) },
    orgId: new Types.ObjectId(orgId),
    isDeleted: false,
  })
    .select(SESSION_FIELDS)
    .lean<SessionWithTitle[]>()
    .exec();

export interface ChatNotificationContextDeps {
  authz: IAuthorizationService;
  teams: ITeamDirectory;
  users: IUserDirectory;
  flags: IFeatureFlags;
  logger: Pick<Logger, 'warn'>;
  loadSessions?: SessionBatchLoader;
}

const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i;

const sessionIdOf = (row: ContextRow): string | undefined => {
  const id = row.payload?.sessionId;
  return typeof id === 'string' && OBJECT_ID_HEX.test(id) ? id : undefined;
};

const isEligible = (row: ContextRow): boolean =>
  row.type.startsWith('chat.') && row.type !== 'chat.deleted';

const capTitle = (title: string | undefined): string | undefined => {
  const chars = Array.from((title ?? '').trim());
  return chars.length === 0
    ? undefined
    : chars.slice(0, CHAT_TITLE_MAX_CHARS).join('');
};

export class ChatNotificationContext implements IChatNotificationContext {
  private readonly loadSessions: SessionBatchLoader;

  constructor(private readonly deps: ChatNotificationContextDeps) {
    this.loadSessions = deps.loadSessions ?? loadSessionsForNotifications;
  }

  async resolve(
    req: AuthenticatedUserRequest,
    rows: readonly ContextRow[],
  ): Promise<ReadonlyMap<number, NotificationContext>> {
    const out = new Map<number, NotificationContext>();
    const candidates = rows.flatMap((row, index) => {
      const sessionId = isEligible(row) ? sessionIdOf(row) : undefined;
      return sessionId === undefined ? [] : [{ row, index, sessionId }];
    });
    if (candidates.length === 0) {
      return out;
    }
    try {
      if (
        !(await this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats))
      ) {
        return out;
      }
      const { userId, orgId } = callerIdentityOf(req);
      if (userId === '' || orgId === '') {
        return out;
      }
      const sessions = await this.loadSessions(orgId, [
        ...new Set(candidates.map((c) => c.sessionId)),
      ]);
      const byId = new Map(sessions.map((s) => [s._id.toString(), s]));
      const projects = await this.loadProjects(orgId, sessions);
      const teams = this.teamsOnce(req);

      const readable = new Map<string, boolean>();
      for (const [sessionId, session] of byId) {
        const loaded: ScopedLoadedChat = {
          session,
          project: session.projectId
            ? (projects.get(session.projectId.toString()) ?? null)
            : null,
        };
        readable.set(
          sessionId,
          await this.canRead(req, { userId, orgId }, loaded, teams),
        );
      }

      const visible = candidates.filter((c) => readable.get(c.sessionId));
      const actorIds = [
        ...new Set(
          visible.flatMap((c) =>
            typeof c.row.payload?.actorUserId === 'string'
              ? [c.row.payload.actorUserId]
              : [],
          ),
        ),
      ];
      const names =
        actorIds.length === 0
          ? new Map<string, string>()
          : await this.deps.users.displayNames(orgId, actorIds, {
              emailFallback: false,
            });

      for (const { row, index, sessionId } of visible) {
        const actorId = row.payload?.actorUserId;
        const chatTitle = capTitle(byId.get(sessionId)?.title);
        const actorName =
          typeof actorId === 'string' ? names.get(actorId) : undefined;
        if (chatTitle !== undefined || actorName) {
          out.set(index, {
            ...(chatTitle !== undefined && { chatTitle }),
            ...(actorName && { actorName }),
          });
        }
      }
      return out;
    } catch (error) {
      this.deps.logger.warn(
        'Notification context lookup failed; listing without it',
        { error: error instanceof Error ? error.message : String(error) },
      );
      return new Map();
    }
  }

  private async loadProjects(
    orgId: string,
    sessions: readonly SessionWithTitle[],
  ): Promise<Map<string, ProjectFacts>> {
    const ids = [
      ...new Set(
        sessions.flatMap((s) => (s.projectId ? [s.projectId.toString()] : [])),
      ),
    ];
    if (ids.length === 0) {
      return new Map();
    }
    const found = await Project.find({
      _id: { $in: ids },
      orgId,
      isDeleted: false,
    }).lean();
    return new Map(found.map((p) => [p._id.toString(), projectFactsOf(p)]));
  }

  /** Teams are fetched at most once per request, and only when a decision needs them. */
  private teamsOnce(req: AuthenticatedUserRequest): () => Promise<TeamIds> {
    let pending: Promise<TeamIds> | undefined;
    return () => {
      pending ??= resolveCallerTeams(this.deps.teams, req);
      return pending;
    };
  }

  private async canRead(
    req: AuthenticatedUserRequest,
    identity: { userId: string; orgId: string },
    loaded: ScopedLoadedChat,
    teams: () => Promise<TeamIds>,
  ): Promise<boolean> {
    const { decision } = await decideChatAccess({
      authz: this.deps.authz,
      identity,
      op: 'read',
      loaded,
      collab: true,
      context: { request: req, requestId: req.context?.requestId ?? '' },
      resolveTeams: teams,
    });
    return decision.allow;
  }
}
