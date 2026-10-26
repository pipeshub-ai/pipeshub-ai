import { getConversationRepository } from '../../../libs/repositories/conversation';
import { isValidId } from '../../../libs/repositories/ids';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import {
  getProjectRepository,
  type ProjectPatch,
} from '../../../libs/repositories/project';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
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
  ProjectRole,
} from '../types/project.interfaces';

/** The subset of project fields `computeRole` needs — satisfied by both a hydrated document and a `.lean()` result. */
type ProjectAccessFields = Pick<
  IProject,
  'orgId' | 'userId' | 'members' | 'visibility'
>;

/** A `.lean()`-read project row enriched with the list endpoint's computed fields. */
export type ProjectListItem = IProject & {
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
    const countByProject = await getConversationRepository().countByProject(
      projectIds.map(String),
    );

    const enriched: ProjectListItem[] = projects.map((p) => ({
      ...p,
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
    const { role } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'editor',
      callerTeamIds,
    );

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

  static async setPinned(
    orgId: string,
    userId: string,
    projectId: string,
    isPinned: boolean,
    callerTeamIds: string[] = [],
  ): Promise<ProjectDto> {
    await this.assertAccess(orgId, userId, projectId, 'editor', callerTeamIds);
    return this.write(projectId, { isPinned });
  }

  static async setArchived(
    orgId: string,
    userId: string,
    projectId: string,
    isArchived: boolean,
    callerTeamIds: string[] = [],
  ): Promise<ProjectDto> {
    await this.assertAccess(orgId, userId, projectId, 'editor', callerTeamIds);
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
  ): Promise<IProjectMember[]> {
    const { project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    return project.members;
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
    const { role } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
    );
    if (role !== 'owner') {
      throw new ForbiddenError('Only the project owner can manage members');
    }

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

  static async removeMember(
    orgId: string,
    userId: string,
    projectId: string,
    memberPrincipalId: string,
    principalType: 'user' | 'team' = 'user',
  ): Promise<ProjectDto> {
    const { role } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
    );
    if (role !== 'owner') {
      throw new ForbiddenError('Only the project owner can manage members');
    }
    const saved = await getProjectRepository().removeMember(
      projectId,
      memberPrincipalId,
      principalType,
    );
    if (!saved) {
      throw new NotFoundError('Project not found');
    }
    return saved;
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
  ): Promise<string[]> {
    return getProjectRepository().listAccessibleIds(orgId, userId);
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
