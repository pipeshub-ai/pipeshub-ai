import { CanonicalRole, maxRole } from './ladder';
import { toCanonical } from './role-mapper';
import { ProjectFacts, Subject } from './types';

export function projectRole(
  project: ProjectFacts,
  subject: Subject,
): CanonicalRole {
  if (project.orgId !== subject.orgId) {
    return 'none';
  }
  if (project.ownerId === subject.userId) {
    return 'owner';
  }
  const teamIds = subject.teamIds === 'unresolved' ? [] : subject.teamIds;
  const granted = project.members
    .filter((m) =>
      m.principalType === 'user'
        ? m.principalId === subject.userId
        : teamIds.includes(m.principalId),
    )
    .map((m) => toCanonical('project', m.role));
  const best = maxRole(...granted);
  if (best !== 'none') {
    return best;
  }
  return project.visibility === 'org' ? 'viewer' : 'none';
}
