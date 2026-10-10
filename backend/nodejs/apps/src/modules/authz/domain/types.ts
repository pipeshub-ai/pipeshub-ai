import { CanonicalRole } from './ladder';

export type TeamIds = readonly string[] | 'unresolved';

export interface Subject {
  userId: string;
  orgId: string;
  teamIds: TeamIds;
}

export interface CollaboratorFact {
  userId?: string;
  teamId?: string;
  accessLevel: string;
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
  /** H2 ceiling; absent means viewer (C-2). */
  projectChatAccess?: string | null;
  /** The project's ACL version; chat decisions inherited through H2 are cached against it. */
  aclVersion?: number;
}

export interface ChatFacts {
  orgId: string;
  ownerId: string;
  sharedWith: readonly CollaboratorFact[];
  projectId: string | null;
  projectVisibility: 'private' | 'project';
  /** Null when the chat has no project or the project no longer exists (H9). */
  project: ProjectFacts | null;
}

export interface ContentFacts {
  consent: boolean;
  chatRole: CanonicalRole;
}

export type AccessPathType = 'owner' | 'direct' | 'team' | 'project';

export interface AccessPath {
  type: AccessPathType;
  ref: string;
  role: CanonicalRole;
}

export interface ChatRoleOptions {
  /** The collaboration flag. Off collapses write to read, caps H2 at viewer and ignores team rows. */
  collab: boolean;
  /** Level the caller needs; team rows are only consulted when earlier paths fall short. Defaults to viewer. */
  needed?: CanonicalRole;
}

export interface ResolvedChatRole {
  role: CanonicalRole;
  via: readonly AccessPath[];
}

/** Only team rows could satisfy the requirement and the caller's teams could not be resolved. */
export interface UnresolvedChatRole {
  unresolved: true;
}

export type ChatRoleResult = ResolvedChatRole | UnresolvedChatRole;
