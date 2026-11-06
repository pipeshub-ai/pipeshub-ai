import type { IProjectDocument } from '../../projects/schema/project.schema';
import { QueryFilter, Types } from 'mongoose';
import { IProject } from '../../projects/types/project.interfaces';
import { ProjectFacts, ProjectMemberFact, Subject } from '../domain/types';

export type ProjectListScope = 'mine' | 'shared' | 'all';

export type ProjectAccessFields = Pick<
  IProject,
  'orgId' | 'userId' | 'members' | 'visibility'
>;

export function projectFactsOf(project: ProjectAccessFields): ProjectFacts {
  const members: ProjectMemberFact[] = project.members.map((m) => ({
    principalType: m.principalType,
    principalId: m.principalId.toString(),
    role: m.role,
  }));
  return {
    orgId: project.orgId.toString(),
    ownerId: project.userId.toString(),
    visibility: project.visibility,
    members,
  };
}

/**
 * The `$or` shared by the project list and `getAccessibleProjectIds`, so both match
 * exactly the projects where `projectRole` is not 'none'. Callers add the org and
 * `isDeleted` conditions.
 */
export function projectAccessFilter(
  subject: Subject,
  scope: ProjectListScope,
): QueryFilter<IProjectDocument>[] {
  const clauses: QueryFilter<IProjectDocument>[] = [];
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
    // Team ids are graph id strings and are stored as such on the member row.
    const teamIds = subject.teamIds === 'unresolved' ? [] : subject.teamIds;
    if (teamIds.length > 0) {
      clauses.push({
        members: {
          $elemMatch: {
            principalType: 'team',
            principalId: { $in: [...teamIds] },
          },
        },
      });
    }
  }
  if (scope === 'all') {
    clauses.push({ visibility: 'org' });
  }
  return clauses;
}
