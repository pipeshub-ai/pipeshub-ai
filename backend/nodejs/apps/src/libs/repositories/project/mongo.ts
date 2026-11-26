import mongoose, { QueryFilter } from 'mongoose';
import {
  IProjectDocument,
  Project,
} from '../../../modules/projects/schema/project.schema';
import { sessionOf } from '../mongo';
import { toPlain } from '../plain';
import type { Tx } from '../uow';
import type {
  NewProject,
  ProjectDto,
  ProjectListQuery,
  ProjectMemberInput,
  ProjectPatch,
  ProjectRepository,
} from './types';

const Types = mongoose.Types;
const dto = (row: unknown): ProjectDto => toPlain<ProjectDto>(row);

/**
 * Filter clauses matching projects where the person is a member directly or
 * through a team. `$elemMatch` keeps type and id on the same member row. Team
 * ids are graph id strings and are matched as stored.
 */
function memberClauses(
  userObjId: mongoose.Types.ObjectId,
  teamIds: string[],
): QueryFilter<IProjectDocument>[] {
  const clauses: QueryFilter<IProjectDocument>[] = [
    {
      members: {
        $elemMatch: { principalType: 'user', principalId: userObjId },
      },
    },
  ];
  if (teamIds.length > 0) {
    clauses.push({
      members: {
        $elemMatch: { principalType: 'team', principalId: { $in: teamIds } },
      },
    });
  }
  return clauses;
}

export class MongoProjectRepository implements ProjectRepository {
  async getLive(id: string): Promise<ProjectDto | null> {
    const row = await Project.findOne({ _id: id, isDeleted: false });
    return row ? dto(row) : null;
  }

  async getDeleted(id: string): Promise<ProjectDto | null> {
    const row = await Project.findOne({ _id: id, isDeleted: true });
    return row ? dto(row) : null;
  }

  async create(
    orgId: string,
    userId: string,
    input: NewProject,
  ): Promise<ProjectDto> {
    const project = new Project({
      orgId: new Types.ObjectId(orgId),
      userId: new Types.ObjectId(userId),
      name: input.name,
      description: input.description,
      icon: input.icon,
      color: input.color,
      instructions: input.instructions,
      knowledgeScope: input.knowledgeScope,
      appliedFilters: input.appliedFilters,
      tools: input.tools,
      linkedKnowledgeBaseId: null,
      members: [],
      lastActivityAt: Date.now(),
    });
    return dto(await project.save());
  }

  async list(
    orgId: string,
    userId: string,
    q: ProjectListQuery,
  ): Promise<{ rows: ProjectDto[]; total: number }> {
    const orgObjId = new Types.ObjectId(orgId);
    const userObjId = new Types.ObjectId(userId);
    const scopeOr: QueryFilter<IProjectDocument>[] = [];
    if (q.scope === 'mine' || q.scope === 'all') {
      scopeOr.push({ userId: userObjId });
    }
    if (q.scope === 'shared' || q.scope === 'all') {
      scopeOr.push(...memberClauses(userObjId, q.teamIds));
    }
    if (q.scope === 'all') {
      scopeOr.push({ visibility: 'org' });
    }

    const filter: QueryFilter<IProjectDocument> = {
      orgId: orgObjId,
      isDeleted: false,
      $or: scopeOr,
    };
    if (q.isArchived !== undefined) {
      // Compare rather than assign so no request-derived value reaches the query.
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-boolean-literal-compare
      filter.isArchived = q.isArchived === true;
    } else if (!q.includeArchived) {
      filter.isArchived = false;
    }
    if (q.search !== undefined && q.search !== '') {
      const escaped = q.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      filter.name = { $regex: escaped, $options: 'i' };
    }

    // Pins are per person, so the sort key is computed for this caller.
    const [rows, total] = await Promise.all([
      Project.aggregate<Record<string, unknown>>([
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
        { $skip: q.skip },
        { $limit: q.limit },
        { $project: { callerPinned: 0 } },
      ]),
      Project.countDocuments(filter),
    ]);
    return { rows: rows.map(dto), total };
  }

  async update(id: string, patch: ProjectPatch): Promise<ProjectDto | null> {
    const project = await Project.findOne({ _id: id, isDeleted: false });
    if (!project) return null;
    Object.assign(project, patch);
    return dto(await project.save());
  }

  async setPinned(
    id: string,
    userId: string,
    pinned: boolean,
    clearLegacyPin: boolean,
  ): Promise<ProjectDto | null> {
    const userObjId = new Types.ObjectId(userId);
    const update = pinned
      ? { $addToSet: { pinnedBy: userObjId } }
      : {
          $pull: { pinnedBy: userObjId },
          ...(clearLegacyPin ? { $set: { isPinned: false } } : {}),
        };
    const row = await Project.findOneAndUpdate(
      { _id: id, isDeleted: false },
      update,
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }

  async markDeleted(
    id: string,
    deletedBy: string,
    tx?: Tx,
  ): Promise<ProjectDto | null> {
    const project = await Project.findOne({ _id: id, isDeleted: false });
    if (!project) return null;
    const session = sessionOf(tx);
    project.isDeleted = true;
    project.deletedBy = new Types.ObjectId(deletedBy);
    return dto(await project.save(session ? { session } : undefined));
  }

  async upsertMembers(
    id: string,
    members: ProjectMemberInput[],
    addedBy: string,
  ): Promise<ProjectDto | null> {
    const project = await Project.findOne({ _id: id, isDeleted: false });
    if (!project) return null;
    const existingByPrincipal = new Map(
      project.members.map((m) => [
        `${m.principalType}:${m.principalId.toString()}`,
        m,
      ]),
    );
    for (const incoming of members) {
      if (
        incoming.principalType === 'user' &&
        incoming.principalId === project.userId.toString()
      ) {
        continue; // owner is implicit, never a member row
      }
      const key = `${incoming.principalType}:${incoming.principalId}`;
      const existing = existingByPrincipal.get(key);
      if (existing) {
        existing.role = incoming.role;
      } else {
        project.members.push({
          principalType: incoming.principalType,
          principalId:
            incoming.principalType === 'team'
              ? incoming.principalId
              : new Types.ObjectId(incoming.principalId),
          role: incoming.role,
          addedBy: new Types.ObjectId(addedBy),
          addedAt: new Date(),
        });
      }
    }
    return dto(await project.save());
  }

  async removeMember(
    id: string,
    principalId: string,
    principalType: 'user' | 'team',
  ): Promise<ProjectDto | null> {
    const project = await Project.findOne({ _id: id, isDeleted: false });
    if (!project) return null;
    project.members = project.members.filter(
      (m) =>
        !(
          m.principalType === principalType &&
          m.principalId.toString() === principalId
        ),
    );
    return dto(await project.save());
  }

  async listWithLinkedKbForMember(
    orgId: string,
    userId: string,
  ): Promise<ProjectDto[]> {
    const rows = await Project.find({
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
    return rows.map(dto);
  }

  async removeUserFromAll(orgId: string, userId: string): Promise<void> {
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

  async listAccessibleIds(
    orgId: string,
    userId: string,
    teamIds: string[],
  ): Promise<string[]> {
    const orgObjId = new Types.ObjectId(orgId);
    const userObjId = new Types.ObjectId(userId);
    const rows = await Project.find(
      {
        orgId: orgObjId,
        isDeleted: false,
        $or: [
          { userId: userObjId },
          ...memberClauses(userObjId, teamIds),
          { visibility: 'org' },
        ],
      },
      { _id: 1 },
    ).lean();
    return rows.map((p) => String(p._id));
  }

  async orgHasTeamMembers(orgId: string): Promise<boolean> {
    const found = await Project.exists({
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
      'members.principalType': 'team',
    });
    return found !== null;
  }

  async isLiveWithoutTeamMembers(id: string): Promise<boolean> {
    const found = await Project.exists({
      _id: new Types.ObjectId(id),
      isDeleted: false,
      'members.principalType': { $ne: 'team' },
    });
    return found !== null;
  }

  async touchActivity(id: string): Promise<void> {
    await Project.updateOne(
      { _id: new Types.ObjectId(id) },
      { $set: { lastActivityAt: Date.now() } },
    );
  }

  async claimLinkedKb(
    id: string,
    previousKbId: string | null,
    kbId: string,
  ): Promise<ProjectDto | null> {
    const row = await Project.findOneAndUpdate(
      { _id: id, linkedKnowledgeBaseId: previousKbId },
      { $set: { linkedKnowledgeBaseId: kbId } },
      { returnDocument: 'after' },
    );
    return row ? dto(row) : null;
  }
}
