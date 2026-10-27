import { Document, Types } from 'mongoose';
import { IAppliedFilterNode } from '../../enterprise_search/types/conversation.interfaces';

export type ProjectMemberRole = 'viewer' | 'editor';
export type ProjectPrincipalType = 'user' | 'team';
export type ProjectVisibility = 'private' | 'org';
/** Owner-controlled default for whether new/existing chats in this project are visible to project members. */
export type ProjectChatSharing = 'private' | 'members';
/** Effective access role computed for the requesting user, or 'none' for an unauthorized caller. */
export type ProjectRole = 'owner' | 'editor' | 'viewer' | 'none';

/** A user member row with the name and email read from the users collection, when the user still exists. */
export type ProjectMemberView = IProjectMember & {
  name?: string;
  email?: string;
};

export interface IProjectMember {
  principalType: ProjectPrincipalType;
  /** userId ObjectId (principalType 'user') or the team's graph id string (principalType 'team'). */
  principalId: Types.ObjectId | string;
  role: ProjectMemberRole;
  addedBy: Types.ObjectId;
  addedAt: Date;
}

/** Raw id-array shape — identical to the `filters` object already sent to the AI backend (see es_validators `filtersSchema`). */
export interface IProjectKnowledgeScope {
  apps?: string[];
  kb?: string[];
  /** Record groups, with the groups nested in them. */
  recordGroups?: string[];
  /** Folders or records, with everything under them. */
  records?: string[];
}

/** Display-friendly mirror of `knowledgeScope`, for rendering scope chips without a round trip. */
export interface IProjectAppliedFilters {
  apps?: IAppliedFilterNode[];
  kb?: IAppliedFilterNode[];
  recordGroups?: IAppliedFilterNode[];
  records?: IAppliedFilterNode[];
}

export interface IProject {
  orgId: Types.ObjectId;
  /** Owner. Ownership never transfers in V1. */
  userId: Types.ObjectId;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  /** Injected into the system prompt as a dedicated `project_instructions` section — see prompt_builder.py. */
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  appliedFilters?: IProjectAppliedFilters;
  /** Tool fullNames (toolset + MCP tools, same format as the composer's `agentStreamTools`) available to this project in agent mode. */
  tools: string[];
  /** Hidden Collection (KB) holding this project's uploaded files — see `ProjectKnowledgeBaseService.ensureLinkedKb`. Created lazily on first upload. */
  linkedKnowledgeBaseId?: string | null;
  visibility: ProjectVisibility;
  chatSharing: ProjectChatSharing;
  members: IProjectMember[];
  /** Legacy project-wide pin; counts only as the owner's pin. See `ProjectService.isPinnedFor`. */
  isPinned: boolean;
  /** Users who pinned this project in their own list. */
  pinnedBy?: Types.ObjectId[];
  isArchived: boolean;
  archivedBy?: Types.ObjectId;
  isDeleted: boolean;
  deletedBy?: Types.ObjectId;
  lastActivityAt: number;
  metadata?: Map<string, unknown>;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface IProjectDocument extends Document, IProject {}

/** Result of an access check against a project — role drives what the caller may do. */
export interface ProjectAccess {
  role: ProjectRole;
  project: IProjectDocument;
}

/** Assembled once per AI call site and merged into the outgoing `aiPayload` by `applyProjectScope`. */
export interface ProjectContext {
  projectId: string;
  instructions?: string;
  knowledgeScope?: IProjectKnowledgeScope;
  /** Tool fullNames the project scope permits in agent mode. */
  tools: string[];
  /** The project's own hidden Collection, if one has been created — always added to the effective `filters.kb`. */
  linkedKnowledgeBaseId?: string | null;
}

/** Sentinel accepted by `?projectId=` on conversation-list endpoints to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';
