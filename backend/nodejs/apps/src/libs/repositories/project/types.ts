/**
 * Project repository: workspaces that group chats under shared instructions,
 * a knowledge scope and members. Rows leave as plain objects with string ids.
 * Who may see or change a project is the service's decision (it computes the
 * caller's role); the repository stores and finds.
 */
import type {
  IProjectAppliedFilters,
  IProjectKnowledgeScope,
  ProjectChatSharing,
  ProjectDto,
  ProjectMemberRole,
  ProjectPrincipalType,
  ProjectVisibility,
} from '../../../modules/projects/types/project.interfaces';
import type { Tx } from '../uow';

export type { ProjectDto };

export interface NewProject {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: IProjectAppliedFilters;
  tools: string[];
}

/** Fields a write may change; a key present with `undefined` clears the stored value. */
export interface ProjectPatch {
  name?: string;
  description?: string;
  icon?: string;
  color?: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: IProjectAppliedFilters;
  tools?: string[];
  visibility?: ProjectVisibility;
  chatSharing?: ProjectChatSharing;
  isArchived?: boolean;
  archivedBy?: string;
}

export interface ProjectListQuery {
  scope: 'mine' | 'shared' | 'all';
  includeArchived: boolean;
  isArchived?: boolean;
  search?: string;
  /** Graph ids of the teams the caller belongs to. */
  teamIds: string[];
  skip: number;
  limit: number;
}

export interface ProjectMemberInput {
  principalId: string;
  principalType: ProjectPrincipalType;
  role: ProjectMemberRole;
}

export interface ProjectRepository {
  /** The project if it is not deleted (any organisation: the caller checks the role). */
  getLive(id: string): Promise<ProjectDto | null>;
  /** The project if it is deleted. */
  getDeleted(id: string): Promise<ProjectDto | null>;
  create(orgId: string, userId: string, input: NewProject): Promise<ProjectDto>;
  /**
   * The caller's pinned projects first (their own pin; a legacy project-wide
   * pin counts only for the owner), then most recent activity; `total`
   * ignores the page.
   */
  list(
    orgId: string,
    userId: string,
    q: ProjectListQuery,
  ): Promise<{ rows: ProjectDto[]; total: number }>;
  /** Writes the patch on the live project; null when there is none. */
  update(id: string, patch: ProjectPatch): Promise<ProjectDto | null>;
  /**
   * Pins or unpins the project in one person's own list; atomic, so two people
   * pinning at once both stick. Unpinning as the owner also clears the legacy
   * project-wide pin (`clearLegacyPin`). Null when there is no live project.
   */
  setPinned(
    id: string,
    userId: string,
    pinned: boolean,
    clearLegacyPin: boolean,
  ): Promise<ProjectDto | null>;
  /** Marks the live project deleted; null when there is none. */
  markDeleted(
    id: string,
    deletedBy: string,
    tx?: Tx,
  ): Promise<ProjectDto | null>;
  /**
   * Adds the members, or changes the role of those already listed. The owner is
   * implicit and never stored as a member. Null when there is no live project.
   */
  upsertMembers(
    id: string,
    members: ProjectMemberInput[],
    addedBy: string,
  ): Promise<ProjectDto | null>;
  removeMember(
    id: string,
    principalId: string,
    principalType: ProjectPrincipalType,
  ): Promise<ProjectDto | null>;
  /** Live projects of this organisation that list the person as a member and have a linked collection. */
  listWithLinkedKbForMember(
    orgId: string,
    userId: string,
  ): Promise<ProjectDto[]>;
  /** Removes the person from every project's members in the organisation. */
  removeUserFromAll(orgId: string, userId: string): Promise<void>;
  /**
   * Ids of live projects the person owns, belongs to (directly or through one of
   * `teamIds`), or can see because they are organisation-wide.
   */
  listAccessibleIds(
    orgId: string,
    userId: string,
    teamIds: string[],
  ): Promise<string[]>;
  /** Whether some live project of the organisation has a team among its members. */
  orgHasTeamMembers(orgId: string): Promise<boolean>;
  /** Whether the project is live and has no team among its members. */
  isLiveWithoutTeamMembers(id: string): Promise<boolean>;
  /** Sets the activity time to now. */
  touchActivity(id: string): Promise<void>;
  /**
   * Links a collection only while the project still holds `previousKbId`
   * (null for none); null when another caller linked one first.
   */
  claimLinkedKb(
    id: string,
    previousKbId: string | null,
    kbId: string,
  ): Promise<ProjectDto | null>;
}
