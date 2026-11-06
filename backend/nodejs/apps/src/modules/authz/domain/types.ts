import { CanonicalRole } from './ladder';

export type TeamIds = readonly string[] | 'unresolved';

export interface Subject {
  userId: string;
  orgId: string;
  teamIds: TeamIds;
}

export interface ProjectMemberFact {
  principalType: 'user' | 'team';
  principalId: string;
  role: string;
}

export interface ProjectFacts {
  orgId: string;
  ownerId: string;
  visibility: 'private' | 'org';
  members: readonly ProjectMemberFact[];
}

export type AccessPathType = 'owner' | 'direct' | 'team' | 'project';

export interface AccessPath {
  type: AccessPathType;
  ref: string;
  role: CanonicalRole;
}
