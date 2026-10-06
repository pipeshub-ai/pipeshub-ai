import { FilterQuery, Types } from 'mongoose';
import {
  IProject,
  IProjectDocument,
  IProjectMember,
} from '../../projects/types/project.interfaces';
import { ProjectFacts, ProjectMemberFact, Subject } from '../domain/types';

export type ProjectListScope = 'mine' | 'shared' | 'all';

export type ProjectAccessFields = Pick<
  IProject,
  'orgId' | 'userId' | 'members' | 'visibility'
> &
  Partial<Pick<IProject, 'projectChatAccess' | 'aclVersion'>>;

/** The id a member row is matched on: the user id, or the team key (legacy team rows kept it in `principalId`). */
export function memberPrincipalKey(member: IProjectMember): string {
  const key =
    member.principalType === 'team'
      ? (member.teamId ?? member.principalId)
      : member.principalId;
  return key === undefined ? '' : String(key);
}

export function projectFactsOf(project: ProjectAccessFields): ProjectFacts {
  const members: ProjectMemberFact[] = project.members.map((m) => ({
    principalType: m.principalType,
    principalId: memberPrincipalKey(m),
    role: m.role,
  }));
  return {
    orgId: project.orgId.toString(),
    ownerId: project.userId.toString(),
    visibility: project.visibility,
    members,
    projectChatAccess: project.projectChatAccess ?? null,
    aclVersion: project.aclVersion ?? 0,
  };
}

/**
 * The `$or` shared by the project list and `getAccessibleProjectIds`, so both match
 * exactly the projects where `projectRole` is not 'none'. Callers add the org and
 * `isDeleted` conditions. Team keys match `teamId` strings; legacy rows keyed by an
 * ObjectId `principalId` are still matched when a team key is a valid ObjectId.
 */
export function projectAccessFilter(
  subject: Subject,
  scope: ProjectListScope,
): FilterQuery<IProjectDocument>[] {
  const clauses: FilterQuery<IProjectDocument>[] = [];
  if (scope === 'mine' || scope === 'all') {
    clauses.push({ userId: new Types.ObjectId(subject.userId) });
  }
  if (scope === 'shared' || scope === 'all') {
    clauses.push({
      members: {
        $elemMatch: {
          principalType: 'user',
          principalId: new Types.ObjectId(subject.userId),
        },
      },
    });
    const teamIds = subject.teamIds === 'unresolved' ? [] : subject.teamIds;
    if (teamIds.length > 0) {
      const legacyIds = teamIds
        .filter((id) => Types.ObjectId.isValid(id))
        .map((id) => new Types.ObjectId(id));
      const byKey: FilterQuery<IProjectMember>[] = [
        { teamId: { $in: [...teamIds] } },
      ];
      if (legacyIds.length > 0) {
        byKey.push({ principalId: { $in: legacyIds } });
      }
      clauses.push({
        members: { $elemMatch: { principalType: 'team', $or: byKey } },
      });
    }
  }
  if (scope === 'all') {
    clauses.push({ visibility: 'org' });
  }
  return clauses;
}
