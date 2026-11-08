import { getConversationRepository } from '../../../libs/repositories/conversation';
import { isValidId } from '../../../libs/repositories/ids';
import { getUserRepository } from '../../../libs/repositories/user';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import {
  getProjectRepository,
  type ProjectPatch,
} from '../../../libs/repositories/project';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  IProject,
  ProjectDto,
  IProjectKnowledgeScope,
  IProjectMember,
  ProjectAccess,
  ProjectContext,
  ProjectMemberView,
  ProjectRole,
} from '../types/project.interfaces';

/** The subset of project fields `computeRole` needs — satisfied by both a hydrated document and a `.lean()` result. */
type ProjectAccessFields = Pick<
  IProject,
  'orgId' | 'userId' | 'members' | 'visibility'
>;

type ProjectPinFields = Pick<IProject, 'userId' | 'isPinned' | 'pinnedBy'>;

/** Refused while a project is archived: archive makes it read-only until the owner restores it. */
export const ARCHIVED_PROJECT_MESSAGE =
  'This project is archived. Unarchive it to make changes or start new chats in it.';

/** A `.lean()`-read project row enriched with the list endpoint's computed fields. */
export type ProjectListItem = Omit<IProject, 'pinnedBy'> & {
  _id: string;
  role: ProjectRole;
  conversationCount: number;
};

const logger = Logger.getInstance({ service: 'ProjectService' });
const unitOfWork = createUnitOfWork(
  process.env.REPLICA_SET_AVAILABLE === 'true',
);

export interface CreateProjectInput {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: ProjectDto['appliedFilters'];
  tools?: string[];
}

export type UpdateProjectInput = Partial<CreateProjectInput> & {
  visibility?: ProjectDto['visibility'];
  chatSharing?: ProjectDto['chatSharing'];
};

export interface ListProjectsOptions {
  page: number;
  limit: number;
  search?: string;
  scope: 'mine' | 'shared' | 'all';
  includeArchived: boolean;
  isArchived?: boolean;
}

const ROLE_RANK: Record<ProjectRole, number> = {
  none: 0,
  viewer: 1,
  editor: 2,
  owner: 3,
};

function hasAtLeastRole(role: ProjectRole, required: ProjectRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[required];
}

/**
 * Owns all project CRUD, access control, membership, and the derived
 * "context" (instructions/knowledgeScope/tools/linkedKnowledgeBaseId) that
 * `applyProjectScope` (enterprise_search/utils/project-context.ts) merges
 * into an AI payload. Mongo-only — the linked hidden Collection's lifecycle
 * and graph permission sync live in `ProjectKnowledgeBaseService`.
 *
 * A caller who cannot see a project gets NotFoundError, the same as for a
 * project that doesn't exist, so a guessed or leaked id cannot be used to
 * enumerate names. A caller who can see it but lacks the role an action needs
 * gets ForbiddenError.
 */
export class ProjectService {
  /**
   * Computes the caller's role without throwing — used by list/read paths
   * that need to filter, not reject. `callerTeamIds` (resolved once per
   * request via `resolveCallerTeamIds` — see `team-membership.ts`) lets a
   * `team` member row grant access the same way a matching `user` row
   * does; omit it (defaults to `[]`) where team membership hasn't been
   * resolved, which only under-grants access, never over-grants it.
   * When both a user row and one or more matching team rows exist, the
   * highest-ranked role among all matches wins.
   */
  static computeRole(
    project: ProjectAccessFields,
    userId: string,
    orgId: string,
    callerTeamIds: string[] = [],
  ): ProjectRole {
    if (project.orgId !== orgId) {
      return 'none';
    }
    if (project.userId === userId) {
      return 'owner';
    }
    const teamIdSet = new Set(callerTeamIds);
    let bestRole: ProjectRole = 'none';
    for (const member of project.members as IProjectMember[]) {
      const matches =
        (member.principalType === 'user' && member.principalId === userId) ||
        (member.principalType === 'team' && teamIdSet.has(member.principalId));
      if (matches && ROLE_RANK[member.role] > ROLE_RANK[bestRole]) {
        bestRole = member.role;
      }
    }
    if (bestRole !== 'none') {
      return bestRole;
    }
    if (project.visibility === 'org') {
      return 'viewer';
    }
    return 'none';
  }

  /**
   * Whether `userId` pinned the project in their own list. A legacy
   * project-wide `isPinned` counts only for the owner, who was the one
   * pinning it in almost every case, so members' lists stop re-sorting.
   */
  static isPinnedFor(project: ProjectPinFields, userId: string): boolean {
    if ((project.pinnedBy ?? []).some((id) => id === userId)) {
      return true;
    }
    return project.isPinned && project.userId === userId;
  }

  /** Throws ConflictError when the project is archived, for the writes archive freezes. */
  static assertNotArchived(project: Pick<IProject, 'isArchived'>): void {
    if (project.isArchived) {
      throw new ConflictError(ARCHIVED_PROJECT_MESSAGE);
    }
  }

  /** Loads a project and asserts the caller has at least `required` role: NotFoundError if they cannot see it, ForbiddenError if their role is too low (see class doc). */
  static async assertAccess(
    orgId: string,
    userId: string,
    projectId: string,
    required: ProjectRole = 'viewer',
    callerTeamIds: string[] = [],
  ): Promise<ProjectAccess> {
    if (!isValidId(projectId)) {
      throw new BadRequestError('Invalid project ID format');
    }
    const project = await getProjectRepository().getLive(projectId);
    if (!project) {
      throw new NotFoundError('Project not found');
    }
    const role = this.computeRole(project, userId, orgId, callerTeamIds);
    if (role === 'none') {
      throw new NotFoundError('Project not found');
    }
    if (!hasAtLeastRole(role, required)) {
      throw new ForbiddenError(
        `This action needs the ${required} role on the project`,
      );
    }
    return { role, project };
  }

  /**
   * True when `projectId` names a project the caller owns that is already
   * soft-deleted. `assertAccess` never loads deleted projects, so delete uses
   * this to answer a repeated delete with success instead of 404. Anyone but
   * the owner still gets false, so a deleted project stays invisible to them.
   */
  static async isDeletedByOwner(
    orgId: string,
    userId: string,
    projectId: string,
  ): Promise<boolean> {
    if (!isValidId(projectId)) {
      return false;
    }
    const project = await getProjectRepository().getDeleted(projectId);
    return (
      project?.isDeleted === true &&
      this.computeRole(project, userId, orgId) === 'owner'
    );
  }

  static async create(
    orgId: string,
    userId: string,
    input: CreateProjectInput,
  ): Promise<ProjectDto> {
    if (!input.name.trim()) {
      throw new BadRequestError('Project name is required');
    }
    return getProjectRepository().create(orgId, userId, {
      name: input.name.trim(),
      description: input.description,
      icon: input.icon,
      color: input.color,
      instructions: input.instructions,
      knowledgeScope: input.knowledgeScope,
      appliedFilters: input.appliedFilters,
      tools: input.tools ?? [],
    });
  }

  static async list(
    orgId: string,
    userId: string,
    opts: ListProjectsOptions,
    callerTeamIds: string[] = [],
  ): Promise<{ projects: ProjectListItem[]; totalCount: number }> {
    const { rows: projects, total: totalCount } =
      await getProjectRepository().list(orgId, userId, {
        scope: opts.scope,
        includeArchived: opts.includeArchived,
        isArchived: opts.isArchived,
        search: opts.search,
        teamIds: callerTeamIds,
        skip: (opts.page - 1) * opts.limit,
        limit: opts.limit,
      });

    const projectIds = projects.map((p) => p._id);
    // Counts only the chats this caller can open, the same rows
    // GET /:projectId/conversations returns: their own, plus chats shared to
    // the project. Counting every chat leaked how many private chats exist.
    const countByProject = await getConversationRepository().countByProject(
      orgId,
      userId,
      projectIds.map(String),
    );

    const enriched: ProjectListItem[] = projects.map((p) => ({
      ...this.toCallerView(p, userId),
      role: this.computeRole(p, userId, orgId, callerTeamIds),
      conversationCount: countByProject.get(p._id) ?? 0,
    }));

    return { projects: enriched, totalCount };
  }

  static async update(
    orgId: string,
    userId: string,
    projectId: string,
    patch: UpdateProjectInput,
    callerTeamIds: string[] = [],
  ): Promise<ProjectDto> {
    const { role, project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'editor',
      callerTeamIds,
    );

    this.assertNotArchived(project);

    if (
      (patch.visibility !== undefined || patch.chatSharing !== undefined) &&
      role !== 'owner'
    ) {
      throw new ForbiddenError(
        'Only the project owner can change sharing settings',
      );
    }

    const changes: ProjectPatch = {};
    if (patch.name !== undefined) {
      if (!patch.name.trim()) {
        throw new BadRequestError('Project name cannot be empty');
      }
      changes.name = patch.name.trim();
    }
    if (patch.description !== undefined)
      changes.description = patch.description;
    if (patch.icon !== undefined) changes.icon = patch.icon;
    if (patch.color !== undefined) changes.color = patch.color;
    if (patch.instructions !== undefined)
      changes.instructions = patch.instructions;
    if (patch.knowledgeScope !== undefined)
      changes.knowledgeScope = patch.knowledgeScope;
    if (patch.appliedFilters !== undefined)
      changes.appliedFilters = patch.appliedFilters;
    if (patch.tools !== undefined) changes.tools = patch.tools;
    if (patch.visibility !== undefined) changes.visibility = patch.visibility;
    if (patch.chatSharing !== undefined)
      changes.chatSharing = patch.chatSharing;

    return this.write(projectId, changes);
  }

  /** Applies the change to the live project; a project removed since the access check answers NotFoundError. */
  private static async write(
    projectId: string,
    changes: ProjectPatch,
  ): Promise<ProjectDto> {
    const saved = await getProjectRepository().update(projectId, changes);
    if (!saved) {
      throw new NotFoundError('Project not found');
    }
    return saved;
  }

  /**
   * Pins or unpins the project in the caller's own list. Anyone who can see
   * the project may do it, and it never re-sorts anyone else's list.
   */
  static async setPinned(
    orgId: string,
    userId: string,
    projectId: string,
    isPinned: boolean,
    callerTeamIds: string[] = [],
  ): Promise<ProjectDto> {
    const { role } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    const saved = await getProjectRepository().setPinned(
      projectId,
      userId,
      isPinned,
      !isPinned && role === 'owner',
    );
    if (!saved) {
      throw new NotFoundError('Project not found');
    }
    return saved;
  }

  /** Owner-only: archiving hides the project from every member's default list and freezes it. */
  static async setArchived(
    orgId: string,
    userId: string,
    projectId: string,
    isArchived: boolean,
    callerTeamIds: string[] = [],
  ): Promise<ProjectDto> {
    const { role } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    if (role !== 'owner') {
      throw new ForbiddenError(
        'Only the project owner can archive or restore this project',
      );
    }
    return this.write(projectId, {
      isArchived,
      archivedBy: isArchived ? userId : undefined,
    });
  }

  /**
   * Idempotent soft delete. Unlinks sessions from the project *before*
   * marking it deleted so a crash between the two steps leaves an
   * already-unlinked project that is safe to retry (sessions never point at
   * a deleted project). Owner-only.
   */
  /**
   * The live project for the owner to delete, or null when the owner already
   * deleted it (so the caller answers the idempotent success). Rechecks after a
   * not-found: a concurrent delete can land between the two lookups.
   */
  static async loadForDelete(
    orgId: string,
    userId: string,
    projectId: string,
  ): Promise<ProjectDto | null> {
    if (await this.isDeletedByOwner(orgId, userId, projectId)) {
      return null;
    }
    let access: ProjectAccess;
    try {
      access = await this.assertAccess(orgId, userId, projectId, 'viewer');
    } catch (error) {
      if (
        error instanceof NotFoundError &&
        (await this.isDeletedByOwner(orgId, userId, projectId))
      ) {
        return null;
      }
      throw error;
    }
    if (access.role !== 'owner') {
      throw new ForbiddenError(
        'Only the project owner can delete this project',
      );
    }
    return access.project;
  }

  static async softDelete(
    orgId: string,
    userId: string,
    projectId: string,
  ): Promise<void> {
    const found = await this.loadForDelete(orgId, userId, projectId);
    if (!found) {
      return;
    }
    const project = found;

    await unitOfWork.run(async (tx) => {
      await getConversationRepository().unlinkProject(project._id, tx);
      await getProjectRepository().markDeleted(project._id, userId, tx);
    });
  }

  /** Assembled once per AI call site by `applyProjectScope`. Returns undefined fields rather than throwing on an empty project. */
  static buildContext(project: ProjectDto): ProjectContext {
    return {
      projectId: project._id,
      instructions: project.instructions?.trim() || undefined,
      knowledgeScope: project.knowledgeScope,
      tools: project.tools ?? [],
      linkedKnowledgeBaseId: project.linkedKnowledgeBaseId ?? null,
    };
  }

  static async listMembers(
    orgId: string,
    userId: string,
    projectId: string,
    callerTeamIds: string[] = [],
  ): Promise<ProjectMemberView[]> {
    const { project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    return this.withMemberNames(orgId, project.members);
  }

  /**
   * Adds each user member's name and email, read in one query, so a client
   * does not resolve every id on its own. Team rows and users that no longer
   * exist come back as stored.
   */
  static async withMemberNames(
    orgId: string,
    members: IProjectMember[],
  ): Promise<ProjectMemberView[]> {
    const userIds = members
      .filter((m) => m.principalType === 'user')
      .map((m) => m.principalId)
      .filter((id) => isValidId(id));
    const users = userIds.length
      ? await getUserRepository().listLiveByIds(orgId, userIds)
      : [];
    const byId = new Map(users.map((u) => [u._id, u]));
    return members.map((m) => {
      const user =
        m.principalType === 'user' ? byId.get(m.principalId) : undefined;
      return user
        ? {
            ...m,
            ...(user.fullName ? { name: user.fullName } : {}),
            email: user.email,
          }
        : m;
    });
  }

  /**
   * The ids in `userIds` that name live users of this org. Read from the
   * store rather than through the IAM API, so the check works with an OAuth
   * token or PAT that carries no user:read scope.
   */
  static async findExistingUserIds(
    orgId: string,
    userIds: string[],
  ): Promise<Set<string>> {
    const valid = userIds.filter((id) => isValidId(id));
    if (valid.length === 0) return new Set();
    const users = await getUserRepository().listLiveByIds(orgId, valid);
    return new Set(users.map((u) => u._id));
  }

  static async upsertMembers(
    orgId: string,
    userId: string,
    projectId: string,
    members: Array<{
      principalId: string;
      principalType?: 'user' | 'team';
      role: 'viewer' | 'editor';
    }>,
  ): Promise<ProjectDto> {
    const { role, project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
    );
    if (role !== 'owner') {
      throw new ForbiddenError('Only the project owner can manage members');
    }
    this.assertNotArchived(project);

    const saved = await getProjectRepository().upsertMembers(
      projectId,
      members.map((m) => ({
        principalId: m.principalId,
        principalType: m.principalType ?? 'user',
        role: m.role,
      })),
      userId,
    );
    if (!saved) {
      throw new NotFoundError('Project not found');
    }
    return saved;
  }

  /**
   * Owner removes anyone; a member may remove only their own user row, which
   * is how they leave. `removed` is false when no row matched, so a client can
   * tell a repeated or mistaken removal from a real one.
   *
   * `beforeSave` runs once a row matched and before the removal is stored: the
   * caller revokes the member's access to the files collection there, so a
   * failed revoke leaves the membership in place to retry instead of a former
   * member who can still read the project's files.
   */
  static async removeMember(
    orgId: string,
    userId: string,
    projectId: string,
    memberPrincipalId: string,
    principalType: 'user' | 'team' = 'user',
    callerTeamIds: string[] = [],
    beforeSave?: (project: ProjectDto) => Promise<void>,
  ): Promise<{ project: ProjectDto; removed: boolean }> {
    const { role, project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    const isLeaving = principalType === 'user' && memberPrincipalId === userId;
    if (role !== 'owner' && !isLeaving) {
      throw new ForbiddenError('Only the project owner can manage members');
    }
    const matched = project.members.some(
      (m) =>
        m.principalType === principalType &&
        m.principalId === memberPrincipalId,
    );
    if (!matched) {
      return { project, removed: false };
    }
    await beforeSave?.(project);
    const saved = await getProjectRepository().removeMember(
      projectId,
      memberPrincipalId,
      principalType,
    );
    if (!saved) {
      throw new NotFoundError('Project not found');
    }
    return { project: saved, removed: true };
  }

  /**
   * Org-wide cleanup hook for the user-offboarding path — removes a
   * departed user from every project's member list. Throws on failure so
   * the caller's deletion flow aborts and can be retried (both this pull
   * and the caller's follow-up KB permission revoke are idempotent, so a
   * retry after a partial failure is safe). Returns the projects that had
   * this user as a member *and* a linked KB, so the caller can revoke the
   * corresponding graph permission — the pull below only touches Mongo.
   */
  /**
   * Returns projects where the user is a member AND a linked KB exists —
   * used by `deleteUser` to revoke KB permissions *before* pulling
   * memberships. Finding first, revoking, then pulling guarantees that a
   * failed revocation leaves the membership intact so a retry still finds
   * the projects.
   */
  static async findProjectsWithLinkedKbForUser(
    orgId: string,
    userId: string,
  ): Promise<ProjectDto[]> {
    return getProjectRepository().listWithLinkedKbForMember(orgId, userId);
  }

  /**
   * Pulls the user from every project's `members` array in this org.
   * Call **after** any KB permission revocations that depend on finding
   * the membership — see `findProjectsWithLinkedKbForUser`.
   */
  static async removeUserFromAllProjects(
    orgId: string,
    userId: string,
  ): Promise<void> {
    await getProjectRepository().removeUserFromAll(orgId, userId);
  }

  /**
   * All project ids the caller has at least viewer access to (owner, member,
   * or org-visible) — used by `buildFilter` / `buildAgentConversationFilter`
   * (enterprise_search/utils/utils.ts) to extend conversation access with
   * the "projectId ∈ accessibleProjectIds && projectVisibility === 'project'"
   * branch, so a chat shared to a project's members shows up for all of them
   * without granting write access to anyone else's chat.
   */
  static async getAccessibleProjectIds(
    orgId: string,
    userId: string,
    callerTeamIds: string[] = [],
  ): Promise<string[]> {
    return getProjectRepository().listAccessibleIds(
      orgId,
      userId,
      callerTeamIds,
    );
  }

  /** True when some live project in the org has a team member row. */
  static async orgHasTeamMembers(orgId: string): Promise<boolean> {
    return getProjectRepository().orgHasTeamMembers(orgId);
  }

  /**
   * A project as one caller should see it: `isPinned` is their own pin, and
   * who else pinned it is not shown.
   */
  static toCallerView<T extends ProjectPinFields>(
    project: T,
    userId: string,
  ): Omit<T, 'pinnedBy'> & { isPinned: boolean } {
    const { pinnedBy: _pinnedBy, ...rest } = project;
    return { ...rest, isPinned: this.isPinnedFor(project, userId) };
  }

  static async touchActivity(projectId: string): Promise<void> {
    // All current callers already pass a projectId that survived
    // `assertAccess` (or a stored id field's `.toString()`), but this
    // is a fire-and-forget best-effort bump with no caller-side validation
    // contract of its own — re-validate defensively rather than build a
    // store query straight from a caller-supplied string (CWE-943 / NoSQL
    // operator injection). The repository casts the id, so the raw string
    // never reaches the query.
    if (!isValidId(projectId)) {
      logger.warn('Skipping project activity touch due to invalid projectId', {
        projectId,
      });
      return;
    }
    try {
      await getProjectRepository().touchActivity(projectId);
    } catch (error) {
      logger.error('Failed to bump project lastActivityAt', {
        projectId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

export default ProjectService;
