import { NextFunction, RequestHandler, Response } from 'express';
import { QueryFilter, Types } from 'mongoose';
import { UnauthorizedError } from '../../../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import {
  CanonicalRole,
  maxRole,
  minRole,
} from '../../../../authz/domain/ladder';
import { legacyAllows } from '../../../../authz/domain/legacy';
import { projectRole } from '../../../../authz/domain/project-rules';
import { toCanonical } from '../../../../authz/domain/role-mapper';
import { AccessPath, ProjectFacts } from '../../../../authz/domain/types';
import { projectFactsOf } from '../../../../authz/loaders/project.loader';
import { IProjectAccessPort } from '../../../../authz/ports/project-access.port';
import { getProjectRepository } from '../../../../../libs/repositories/project';
import { EXCLUDE_AGENT, ONLY_AGENT } from '../../../constants/constants';
import { ChatSession } from '../../../schema/chat.session.schema';
import { IChatSession } from '../../../types/conversation.interfaces';
import { ConversationNotFoundError } from '../domain/errors';
import { Caller, ConversationOperation, GrantedRole } from '../domain/types';
import { ScopedSession } from '../scoped-session';
import { setConversationContext } from './conversation-context';
import {
  GuardKind,
  IConversationGuards,
  ListKind,
  ListScopeOptions,
  markGuard,
  resolveOption,
  RunLeaseOptions,
} from './conversation-guards';

const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i;
const SCOPED_FIELDS =
  'orgId userId isDeleted isShared sharedWith projectId projectVisibility initiator isArchived agentKey';

type SessionFilter = QueryFilter<IChatSession>;

export interface DefaultConversationGuardsDeps {
  projects: IProjectAccessPort;
  /**
   * The caller's teams, for project membership through a team. Absent, a project is reached
   * only by direct membership.
   */
  teamIdsOf?: (req: AuthenticatedUserRequest) => Promise<string[]>;
}

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);

const idString = (value: unknown): string | undefined => {
  if (value === null || value === undefined) return undefined;
  const text =
    typeof value === 'string' ? value : (value as Types.ObjectId).toString();
  return text.length > 0 ? text : undefined;
};

/**
 * The owner-centred conversation policy: the owner may do everything; a user a chat was shared
 * with, and a member of a project the chat is shared to, may read it (a recipient may also rate
 * its answers). Every share is read-only, a team share row on the chat is ignored (a team member of its project reads it as a member), and every denial is a 404 that
 * does not tell a stranger whether the conversation exists.
 */
export class DefaultConversationGuards implements IConversationGuards {
  constructor(private readonly deps: DefaultConversationGuardsDeps) {}

  authorize(op: ConversationOperation, kind: GuardKind): RequestHandler {
    return markGuard(
      this.guard((req) => this.authorizeRequest(req, op, kind)),
      { op, kind },
    );
  }

  /** There are no turn preconditions beyond `authorize`. */
  runLease(kind: GuardKind, _options: RunLeaseOptions = {}): RequestHandler {
    return markGuard(
      (_req: AuthenticatedUserRequest, _res: Response, next: NextFunction) =>
        next(),
      { lease: true, kind },
    );
  }

  listScope(kind: ListKind, options: ListScopeOptions = {}): RequestHandler {
    return markGuard(
      this.guard(async (req) => {
        const base = this.identityOf(req);
        const includeOwned = resolveOption(options.includeOwned, req, false);
        const includeShared = resolveOption(options.includeShared, req, false);
        const needsProjects =
          includeShared && resolveOption(options.includeProjects, req, false);
        // A team member of a project reaches the chats shared to it, as a direct member does.
        const accessibleProjectIds = needsProjects
          ? await this.deps.projects.accessibleProjectIds({
              ...base,
              teamIds: await this.teamIdsOf(req),
            })
          : [];
        const caller: Caller = { ...base, teamIds: [] };
        const target = { kind, agentKey: req.params.agentKey };
        const listFilter = readFilter(caller, target, {
          accessibleProjectIds,
          includeOwned,
          includeShared,
          includeShareRows:
            options.shareRows !== 'never' &&
            options.shareRows !== 'extendedOnly',
        });
        const sharedListFilter = options.sharedWithMeList
          ? readFilter(caller, target, {
              accessibleProjectIds: [],
              includeOwned: false,
              includeShared: true,
              includeShareRows: true,
            })
          : undefined;
        setConversationContext(req, {
          caller,
          listFilter,
          sharedListFilter,
          accessibleProjectIds,
        });
      }),
      { list: true, kind },
    );
  }

  caller(): RequestHandler {
    return markGuard(
      this.guard((req) => {
        const { userId, orgId } = this.identityOf(req);
        setConversationContext(req, {
          caller: { userId, orgId, teamIds: 'unresolved' },
        });
      }),
      { caller: true },
    );
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
  ): Promise<void> {
    const { userId, orgId } = this.identityOf(req);
    const id = req.params.conversationId;
    const agentKey = kind === 'agent' ? req.params.agentKey : undefined;
    if (id === undefined || !OBJECT_ID_HEX.test(id)) {
      throw new ConversationNotFoundError();
    }
    if (kind === 'agent' && (agentKey ?? '') === '') {
      throw new ConversationNotFoundError();
    }
    const session = await ChatSession.findOne({
      _id: id,
      orgId,
      isDeleted: false,
      ...(kind === 'chat' ? EXCLUDE_AGENT : { ...ONLY_AGENT, agentKey }),
    })
      .select(SCOPED_FIELDS)
      .lean<ScopedSession>();
    if (!session) {
      throw new ConversationNotFoundError();
    }
    const project = await this.projectOf(session);
    // Teams count only toward the chat's project; a team share row on the chat grants nothing here.
    const teamIds =
      project !== null && session.projectVisibility === 'project'
        ? await this.teamIdsOf(req)
        : [];

    const via = accessPaths(session, project, { userId, orgId, teamIds });
    if (!legacyAllows(kind, op, via)) {
      throw new ConversationNotFoundError();
    }
    const caller: Caller = { userId, orgId, teamIds: 'unresolved' };
    setConversationContext(req, {
      caller,
      grant: {
        session,
        role: grantedRole(maxRole(...via.map((p) => p.role))),
        via,
        caller,
      },
    });
  }

  /** Resolved only when some project of the org has a team member, as the chat lists did. */
  private async teamIdsOf(req: AuthenticatedUserRequest): Promise<string[]> {
    if (!this.deps.teamIdsOf) return [];
    const { orgId } = this.identityOf(req);
    return (await this.deps.projects.orgHasTeamMembers?.(orgId))
      ? this.deps.teamIdsOf(req)
      : [];
  }

  private async projectOf(
    session: ScopedSession,
  ): Promise<ProjectFacts | null> {
    if (!session.projectId) {
      return null;
    }
    const project = await getProjectRepository().getLive(
      session.projectId.toString(),
    );
    return project && project.orgId === session.orgId.toString()
      ? projectFactsOf(project)
      : null;
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
}

const grantedRole = (role: CanonicalRole): GrantedRole =>
  role === 'owner' ? 'owner' : 'read';

/** Every path to the conversation, each capped at read: the owner, a user share row, and the chat's project. */
export function accessPaths(
  session: ScopedSession,
  project: ProjectFacts | null,
  caller: { userId: string; orgId: string; teamIds: readonly string[] },
): AccessPath[] {
  if (session.orgId.toString() !== caller.orgId) {
    return [];
  }
  const paths: AccessPath[] = [];
  const ownerId = session.userId.toString();
  if (ownerId === caller.userId) {
    paths.push({ type: 'owner', ref: ownerId, role: 'owner' });
  }
  // The owner archiving an agent chat takes it away from the people it was shared with,
  // and archiving any chat takes it away from the project's members.
  const archived = session.isArchived === true;
  // A share row grants only while the chat is shared: `isShared` alone, or a row alone, is not enough.
  const sharedRowsCount =
    session.isShared === true && !(archived && session.agentKey !== undefined);
  for (const row of sharedRowsCount ? (session.sharedWith ?? []) : []) {
    const rowUserId = idString(row.userId);
    if (rowUserId !== undefined && rowUserId === caller.userId) {
      paths.push({
        type: 'direct',
        ref: caller.userId,
        role: minRole(
          toCanonical('chat', row.accessLevel === 'write' ? 'write' : 'read'),
          'viewer',
        ),
      });
    }
  }
  const projectId = session.projectId?.toString();
  if (
    session.projectVisibility === 'project' &&
    projectId &&
    project &&
    !archived
  ) {
    const role = minRole(projectRole(project, caller), 'viewer');
    if (role !== 'none') {
      paths.push({ type: 'project', ref: projectId, role });
    }
  }
  return paths.filter((p) => p.role !== 'none');
}

const NO_MATCH: SessionFilter = { $and: [{ _id: { $in: [] } }] };

interface ReadFilterOptions {
  accessibleProjectIds: readonly string[];
  includeOwned: boolean;
  includeShared: boolean;
  includeShareRows: boolean;
}

/** Always `{ $and: [clause] }` so a caller appending conditions cannot overwrite the access `$or`. */
export function readFilter(
  caller: Caller,
  target: { kind: ListKind; agentKey?: string },
  o: ReadFilterOptions,
): SessionFilter {
  const branches: Record<string, unknown>[] = [];
  if (o.includeOwned) {
    branches.push({ userId: oid(caller.userId) });
  }
  if (o.includeShared) {
    if (o.includeShareRows) {
      branches.push({
        isShared: true,
        sharedWith: { $elemMatch: { userId: oid(caller.userId) } },
      });
    }
    if (o.accessibleProjectIds.length > 0) {
      branches.push({
        projectVisibility: 'project',
        projectId: { $in: o.accessibleProjectIds.map(oid) },
      });
    }
  }
  if (branches.length === 0) {
    return NO_MATCH;
  }
  return {
    $and: [
      {
        orgId: oid(caller.orgId),
        isDeleted: false,
        ...kindClause(target),
        $or: branches,
      },
    ],
  };
}

function kindClause(target: {
  kind: ListKind;
  agentKey?: string;
}): Record<string, unknown> {
  if (target.kind === 'any') {
    return {};
  }
  if (target.kind === 'chat') {
    return { ...EXCLUDE_AGENT };
  }
  if (target.agentKey === undefined || target.agentKey === '') {
    throw new Error('An agent filter needs an agentKey');
  }
  return { ...ONLY_AGENT, agentKey: target.agentKey };
}
