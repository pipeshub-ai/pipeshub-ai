import mongoose, { ClientSession, QueryFilter, Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Project } from '../schema/project.schema';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import { Users } from '../../user_management/schema/users.schema';
import {
  IProject,
  IProjectDocument,
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
export type ProjectListItem = IProject & {
  _id: Types.ObjectId;
  role: ProjectRole;
  conversationCount: number;
};

const logger = Logger.getInstance({ service: 'ProjectService' });
const rsAvailable = process.env.REPLICA_SET_AVAILABLE === 'true';

export interface CreateProjectInput {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: IProjectDocument['appliedFilters'];
  tools?: string[];
}

export type UpdateProjectInput = Partial<CreateProjectInput> & {
  visibility?: IProjectDocument['visibility'];
  chatSharing?: IProjectDocument['chatSharing'];
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
 * Filter clauses matching projects where the caller is a member directly or
 * through a team. `$elemMatch` keeps type and id on the same member row. Team
 * ids are graph id strings and are matched as stored.
 */
function memberClauses(
  userObjId: Types.ObjectId,
  callerTeamIds: string[],
): QueryFilter<IProjectDocument>[] {
  const clauses: QueryFilter<IProjectDocument>[] = [
    {
      members: {
        $elemMatch: { principalType: 'user', principalId: userObjId },
      },
    },
  ];
  if (callerTeamIds.length > 0) {
    clauses.push({
      members: {
        $elemMatch: {
          principalType: 'team',
          principalId: { $in: callerTeamIds },
        },
      },
    });
  }
  return clauses;
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
    if (project.orgId.toString() !== orgId) {
      return 'none';
    }
    if (project.userId.toString() === userId) {
      return 'owner';
    }
    const teamIdSet = new Set(callerTeamIds);
    let bestRole: ProjectRole = 'none';
    for (const member of project.members as IProjectMember[]) {
      const matches =
        (member.principalType === 'user' &&
          member.principalId.toString() === userId) ||
        (member.principalType === 'team' &&
          teamIdSet.has(member.principalId.toString()));
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
    if ((project.pinnedBy ?? []).some((id) => id.toString() === userId)) {
      return true;
    }
    return project.isPinned && project.userId.toString() === userId;
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
    if (!mongoose.Types.ObjectId.isValid(projectId)) {
      throw new BadRequestError('Invalid project ID format');
    }
    const project = await Project.findOne({
      _id: projectId,
      isDeleted: false,
    });
    if (!project) {
      throw new NotFoundError('Project not found');
    }
    const role = this.computeRole(project, userId, orgId, callerTeamIds);
    if (role === 'none') {
      throw new NotFoundError('Project not found');
    }
    if (!hasAtLeastRole(role, required)) {
      throw new ForbiddenError(`This action needs the ${required} role on the project`);
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
    if (!mongoose.Types.ObjectId.isValid(projectId)) {
      return false;
    }
    const project = await Project.findOne({ _id: projectId, isDeleted: true });
    return project?.isDeleted === true && this.computeRole(project, userId, orgId) === 'owner';
  }

  static async create(
    orgId: string,
    userId: string,
    input: CreateProjectInput,
  ): Promise<IProjectDocument> {
    if (!input.name.trim()) {
      throw new BadRequestError('Project name is required');
    }
    const project = new Project({
      orgId: new Types.ObjectId(orgId),
      userId: new Types.ObjectId(userId),
      name: input.name.trim(),
      description: input.description,
      icon: input.icon,
      color: input.color,
      instructions: input.instructions,
      knowledgeScope: input.knowledgeScope,
      appliedFilters: input.appliedFilters,
      tools: input.tools ?? [],
      linkedKnowledgeBaseId: null,
      members: [],
      lastActivityAt: Date.now(),
    });
    return project.save();
  }

  static async list(
    orgId: string,
    userId: string,
    opts: ListProjectsOptions,
    callerTeamIds: string[] = [],
  ): Promise<{ projects: ProjectListItem[]; totalCount: number }> {
    const orgObjId = new Types.ObjectId(orgId);
    const userObjId = new Types.ObjectId(userId);

    const scopeOr: QueryFilter<IProjectDocument>[] = [];
    if (opts.scope === 'mine' || opts.scope === 'all') {
      scopeOr.push({ userId: userObjId });
    }
    if (opts.scope === 'shared' || opts.scope === 'all') {
      scopeOr.push(...memberClauses(userObjId, callerTeamIds));
    }
    if (opts.scope === 'all') {
      scopeOr.push({ visibility: 'org' });
    }

    const filter: QueryFilter<IProjectDocument> = {
      orgId: orgObjId,
      isDeleted: false,
      $or: scopeOr,
    };
    if (opts.isArchived !== undefined) {
      // Compare rather than assign so no request-derived value reaches the query.
      filter.isArchived = opts.isArchived === true;
    } else if (!opts.includeArchived) {
      filter.isArchived = false;
    }
    if (opts.search) {
      const escaped = opts.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      filter.name = { $regex: escaped, $options: 'i' };
    }

    const skip = (opts.page - 1) * opts.limit;
    // Pins are per user, so the sort key is computed for this caller.
    const [projects, totalCount] = await Promise.all([
      Project.aggregate<IProject & { _id: Types.ObjectId }>([
        { $match: filter },
        {
          $addFields: {
            callerPinned: {
              $or: [
                { $in: [userObjId, { $ifNull: ['$pinnedBy', []] }] },
                {
                  $and: [
                    { $eq: ['$isPinned', true] },
                    { $eq: ['$userId', userObjId] },
                  ],
                },
              ],
            },
          },
        },
        { $sort: { callerPinned: -1, lastActivityAt: -1, _id: -1 } },
        { $skip: skip },
        { $limit: opts.limit },
        { $project: { callerPinned: 0 } },
      ]),
      Project.countDocuments(filter),
    ]);

    const projectIds = projects.map((p) => p._id);
    // Counts only the chats this caller can open, the same rows
    // GET /:projectId/conversations returns: their own, plus chats shared to
    // the project. Counting every chat leaked how many private chats exist.
    const counts = projectIds.length
      ? await ChatSession.aggregate<{ _id: Types.ObjectId; count: number }>([
          {
            $match: {
              orgId: orgObjId,
              projectId: { $in: projectIds },
              isDeleted: false,
              $or: [{ userId: userObjId }, { projectVisibility: 'project' }],
            },
          },
          { $group: { _id: '$projectId', count: { $sum: 1 } } },
        ])
      : [];
    const countByProject = new Map<string, number>(
      counts.map((c) => [c._id.toString(), c.count]),
    );

    const enriched: ProjectListItem[] = projects.map((p) => ({
      ...this.toCallerView(p, userId),
      _id: p._id,
      role: this.computeRole(p, userId, orgId, callerTeamIds),
      conversationCount: countByProject.get(p._id.toString()) || 0,
    }));

    return { projects: enriched, totalCount };
  }

  static async update(
    orgId: string,
    userId: string,
    projectId: string,
    patch: UpdateProjectInput,
    callerTeamIds: string[] = [],
  ): Promise<IProjectDocument> {
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

    if (patch.name !== undefined) {
      if (!patch.name.trim()) {
        throw new BadRequestError('Project name cannot be empty');
      }
      project.name = patch.name.trim();
    }
    if (patch.description !== undefined)
      project.description = patch.description;
    if (patch.icon !== undefined) project.icon = patch.icon;
    if (patch.color !== undefined) project.color = patch.color;
    if (patch.instructions !== undefined)
      project.instructions = patch.instructions;
    if (patch.knowledgeScope !== undefined)
      project.knowledgeScope = patch.knowledgeScope;
    if (patch.appliedFilters !== undefined)
      project.appliedFilters = patch.appliedFilters;
    if (patch.tools !== undefined) project.tools = patch.tools;
    if (patch.visibility !== undefined) project.visibility = patch.visibility;
    if (patch.chatSharing !== undefined)
      project.chatSharing = patch.chatSharing;

    return project.save();
  }

  /**
   * Pins or unpins the project in the caller's own list. Anyone who can see
   * the project may do it, and it never re-sorts anyone else's list. The
   * array update is atomic so two members pinning at once both stick.
   */
  static async setPinned(
    orgId: string,
    userId: string,
    projectId: string,
    isPinned: boolean,
    callerTeamIds: string[] = [],
  ): Promise<IProjectDocument> {
    const { role, project } = await this.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
      callerTeamIds,
    );
    const userObjId = new Types.ObjectId(userId);
    const update = isPinned
      ? { $addToSet: { pinnedBy: userObjId } }
      : {
          $pull: { pinnedBy: userObjId },
          ...(role === 'owner' ? { $set: { isPinned: false } } : {}),
        };
    const updated = await Project.findOneAndUpdate(
      { _id: project._id, isDeleted: false },
      update,
      { returnDocument: 'after' },
    );
    if (!updated) {
      throw new NotFoundError('Project not found');
    }
    return updated;
  }

  /** Owner-only: archiving hides the project from every member's default list and freezes it. */
  static async setArchived(
    orgId: string,
    userId: string,
    projectId: string,
    isArchived: boolean,
    callerTeamIds: string[] = [],
  ): Promise<IProjectDocument> {
    const { role, project } = await this.assertAccess(
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
    project.isArchived = isArchived;
    project.archivedBy = isArchived ? new Types.ObjectId(userId) : undefined;
    return project.save();
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
  ): Promise<IProjectDocument | null> {
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

    async function unlinkAndDelete(
      session?: ClientSession | null,
    ): Promise<void> {
      await ChatSession.updateMany(
        { projectId: project._id },
        { $unset: { projectId: '', projectVisibility: '' } },
        session ? { session } : undefined,
      );
      project.isDeleted = true;
      project.deletedBy = new Types.ObjectId(userId);
      await project.save(session ? { session } : undefined);
    }

    if (rsAvailable) {
      const session = await mongoose.startSession();
      try {
        session.startTransaction();
        await unlinkAndDelete(session);
        await session.commitTransaction();
      } catch (error) {
        await session.abortTransaction();
        throw error;
      } finally {
        await session.endSession();
      }
    } else {
      await unlinkAndDelete();
    }
  }

  /** Assembled once per AI call site by `applyProjectScope`. Returns undefined fields rather than throwing on an empty project. */
  static buildContext(project: IProjectDocument): ProjectContext {
    return {
      projectId: (project._id as Types.ObjectId).toString(),
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
      .map((m) => m.principalId.toString())
      .filter((id) => Types.ObjectId.isValid(id))
      .map((id) => new Types.ObjectId(id));
    const users = userIds.length
      ? await Users.find(
          {
            _id: { $in: userIds },
            orgId: new Types.ObjectId(orgId),
            isDeleted: false,
          },
          { fullName: 1, email: 1 },
        ).lean()
      : [];
    const byId = new Map(users.map((u) => [String(u._id), u]));
    return members.map((m) => {
      // Rows of a hydrated project are subdocuments; spread their plain form.
      const asDoc = m as { toObject?: () => IProjectMember };
      const plain = asDoc.toObject ? asDoc.toObject() : m;
      const user =
        m.principalType === 'user'
          ? byId.get(m.principalId.toString())
          : undefined;
      return user
        ? {
            ...plain,
            ...(user.fullName ? { name: user.fullName } : {}),
            email: user.email,
          }
        : plain;
    });
  }

  /**
   * The ids in `userIds` that name live users of this org. Read from Mongo
   * rather than through the IAM API, so the check works with an OAuth token
   * or PAT that carries no user:read scope.
   */
  static async findExistingUserIds(
    orgId: string,
    userIds: string[],
  ): Promise<Set<string>> {
    const valid = userIds.filter((id) => Types.ObjectId.isValid(id));
    if (valid.length === 0) return new Set();
    const users = await Users.find(
      {
        _id: { $in: valid.map((id) => new Types.ObjectId(id)) },
        orgId: new Types.ObjectId(orgId),
        isDeleted: false,
      },
      { _id: 1 },
    ).lean();
    return new Set(users.map((u) => String(u._id)));
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
  ): Promise<IProjectDocument> {
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

    const existingByPrincipal = new Map(
      project.members.map((m) => [
        `${m.principalType}:${m.principalId.toString()}`,
        m,
      ]),
    );
    for (const incoming of members) {
      const principalType = incoming.principalType ?? 'user';
      if (
        principalType === 'user' &&
        incoming.principalId === project.userId.toString()
      ) {
        continue; // owner is implicit, never a member row
      }
      const key = `${principalType}:${incoming.principalId}`;
      const existing = existingByPrincipal.get(key);
      if (existing) {
        existing.role = incoming.role;
      } else {
        project.members.push({
          principalType,
          principalId:
            principalType === 'team'
              ? incoming.principalId
              : new Types.ObjectId(incoming.principalId),
          role: incoming.role,
          addedBy: new Types.ObjectId(userId),
          addedAt: new Date(),
        });
      }
    }
    return project.save();
  }

  /**
   * Owner removes anyone; a member may remove only their own user row, which
   * is how they leave. `removed` is false when no row matched, so a client can
   * tell a repeated or mistaken removal from a real one.
   *
   * `beforeSave` runs once a row matched and before the removal is saved: the
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
    beforeSave?: (project: IProjectDocument) => Promise<void>,
  ): Promise<{ project: IProjectDocument; removed: boolean }> {
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
    const before = project.members.length;
    project.members = project.members.filter(
      (m) =>
        !(
          m.principalType === principalType &&
          m.principalId.toString() === memberPrincipalId
        ),
    );
    const removed = project.members.length < before;
    if (!removed) {
      return { project, removed };
    }
    await beforeSave?.(project);
    return { project: await project.save(), removed };
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
  ): Promise<IProjectDocument[]> {
    return Project.find({
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
      members: {
        $elemMatch: {
          principalType: 'user',
          principalId: new Types.ObjectId(userId),
        },
      },
      linkedKnowledgeBaseId: { $ne: null },
    });
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
    await Project.updateMany(
      { orgId: new Types.ObjectId(orgId) },
      {
        $pull: {
          members: {
            principalType: 'user',
            principalId: new Types.ObjectId(userId),
          },
        },
      },
    );
  }

  /** True when some live project in the org has a team member row. */
  static async orgHasTeamMembers(orgId: string): Promise<boolean> {
    const found = await Project.exists({
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
      'members.principalType': 'team',
    });
    return found !== null;
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
  ): Promise<Types.ObjectId[]> {
    const orgObjId = new Types.ObjectId(orgId);
    const userObjId = new Types.ObjectId(userId);
    const projects = await Project.find(
      {
        orgId: orgObjId,
        isDeleted: false,
        $or: [
          { userId: userObjId },
          ...memberClauses(userObjId, callerTeamIds),
          { visibility: 'org' },
        ],
      },
      { _id: 1 },
    ).lean();
    return projects.map((p) => p._id as Types.ObjectId);
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
    // `assertAccess` (or a Mongoose ObjectId field's `.toString()`), but this
    // is a fire-and-forget best-effort bump with no caller-side validation
    // contract of its own — re-validate defensively rather than build a
    // Mongo filter straight from a caller-supplied string (CWE-943 / NoSQL
    // operator injection). The cast to `Types.ObjectId`, not just the
    // `isValid` check, is what removes the raw string from the query.
    if (!Types.ObjectId.isValid(projectId)) {
      logger.warn('Skipping project activity touch due to invalid projectId', {
        projectId,
      });
      return;
    }
    const projectObjectId = new Types.ObjectId(projectId);
    try {
      await Project.updateOne(
        { _id: projectObjectId },
        { $set: { lastActivityAt: Date.now() } },
      );
    } catch (error) {
      logger.error('Failed to bump project lastActivityAt', {
        projectId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

export default ProjectService;
